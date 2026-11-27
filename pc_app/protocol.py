"""Keypad serial protocol v3.

Line based, UTF-8, '\n' terminated, 115200 baud.

ESP32 -> PC
    KEY:<char>              e.g. KEY:1, KEY:#
    MACRO:<action>          legacy action name (host app ignores it, PS1 uses it)
    ENC1:<pos> / ENC2:<pos> absolute encoder position
    ENC1_SW:1 / ENC2_SW:1   encoder button press
    PROFILE:<n>:<name>      active page/profile changed (1 based)
    DISPLAY:<text>          echo of H: test text
    WP:BEGIN:<bytes>        wallpaper transfer accepted
    WP:ACK:<block>          wallpaper block stored
    WP:OK:<bytes>           wallpaper committed and drawn
    WP:TRUNC:<recv>:<total> wallpaper incomplete
    WP:BAD                  wallpaper size rejected

PC -> ESP32
    T:CPU=..;GPU=..;RAM=..;TEMP=.. telemetry for on-device display (1 Hz)
    V:E1=42;E2=17;M1=0;M2=0  mixer percentages + mute flags (display rings)
    P:<n>                   switch page (1..3)
    N:<n>:<name>            set page name shown on the display
    H:<text>                show test text for 5 s
    A:RRGGBB                theme accent colour
    F:<key>                 pulse the accent key badge
    W:B:<bytes>             wallpaper transfer begin (raw big-endian RGB565)
    W:<block>:<base64>      wallpaper block, 768 raw bytes -> 1024 base64 chars
    W:E                     wallpaper transfer end -> commit + redraw
"""

import base64

# 3 rows x 4 columns, physical order on the keypad (rows are driven LOW).
KEY_MATRIX = (
    ("1", "2", "3", "4"),
    ("5", "6", "7", "8"),
    ("9", "*", "0", "#"),
)

PROFILE_COUNT = 3


def key_to_row_col(ch):
    for row, line in enumerate(KEY_MATRIX):
        for col, value in enumerate(line):
            if value == ch:
                return row, col
    return None


def _to_int(text):
    try:
        return int(text)
    except (TypeError, ValueError):
        return 0


def parse_line(line):
    """Parse one line from the device into an (kind, payload) tuple."""
    line = (line or "").strip()
    if not line:
        return ("empty", {})

    if line.startswith("KEY:"):
        ch = line[4:5]
        rc = key_to_row_col(ch)
        if rc is None:
            # e.g. KEY:IGNORED:<c> from the firmware ghost guard - not a key press
            return ("empty", {})
        return ("key", {"char": ch, "row": rc[0], "col": rc[1]})

    if line.startswith("ENC1:"):
        return ("enc", {"id": 1, "pos": _to_int(line[5:])})
    if line.startswith("ENC2:"):
        return ("enc", {"id": 2, "pos": _to_int(line[5:])})
    if line == "ENC1_SW:1":
        return ("enc_sw", {"id": 1})
    if line == "ENC2_SW:1":
        return ("enc_sw", {"id": 2})

    if line.startswith("PROFILE:"):
        parts = line.split(":", 2)
        return ("profile", {"index": _to_int(parts[1]) - 1,
                            "name": parts[2] if len(parts) > 2 else ""})

    if line.startswith("MACRO:"):
        return ("macro", {"action": line[6:]})
    if line.startswith("DISPLAY:"):
        return ("display", {"text": line[8:]})

    if line.startswith("WP:"):
        parts = line.split(":")
        return ("wp", {
            "state": parts[1].lower() if len(parts) > 1 else "",
            "value": _to_int(parts[2]) if len(parts) > 2 else 0,
            "extra": _to_int(parts[3]) if len(parts) > 3 else 0,
        })

    return ("raw", {"line": line})


def cmd_page(n):
    return "P:%d\n" % n


def cmd_page_name(n, name):
    return "N:%d:%s\n" % (n, name)


def cmd_display_text(text):
    return "H:%s\n" % text


def cmd_telemetry(cpu=None, gpu=None, ram=None, temp=None):
    parts = []
    if cpu is not None:
        parts.append("CPU=%d" % cpu)
    if gpu is not None:
        parts.append("GPU=%d" % gpu)
    if ram is not None:
        parts.append("RAM=%d" % ram)
    if temp is not None:
        parts.append("TEMP=%d" % temp)
    return "T:" + ";".join(parts) + "\n"


def cmd_values(enc1=None, enc2=None, muted1=False, muted2=False):
    """Mixer percentages for the on-device encoder rings."""
    parts = []
    if enc1 is not None:
        parts.append("E1=%d" % max(0, min(100, int(enc1))))
        parts.append("M1=%d" % (1 if muted1 else 0))
    if enc2 is not None:
        parts.append("E2=%d" % max(0, min(100, int(enc2))))
        parts.append("M2=%d" % (1 if muted2 else 0))
    return "V:" + ";".join(parts) + "\n"


def cmd_accent(color):
    """Theme accent used for the on-device key flash (RRGGBB)."""
    value = str(color or "").lstrip("#").upper()
    if len(value) != 6 or any(c not in "0123456789ABCDEF" for c in value):
        value = "A855F7"
    return "A:%s\n" % value


def cmd_flash(key=""):
    """Ask the device to pulse its accent key badge (GUI key click)."""
    char = str(key or "-")[:1]
    return "F:%s\n" % char


# Wallpaper blocks: 768 raw bytes -> 1024 base64 chars + header = 1032 < rxLine.
WP_BLOCK_BYTES = 768


def wallpaper_block_count(raw):
    return (len(raw) + WP_BLOCK_BYTES - 1) // WP_BLOCK_BYTES


def wallpaper_begin(raw):
    return "W:B:%d\n" % len(raw)


def wallpaper_block(raw, index):
    chunk = raw[index * WP_BLOCK_BYTES:(index + 1) * WP_BLOCK_BYTES]
    return "W:%d:%s\n" % (index, base64.b64encode(chunk).decode("ascii"))


def wallpaper_end():
    return "W:E\n"
