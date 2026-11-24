"""Keypad serial protocol v2.

Line based, UTF-8, '\n' terminated, 115200 baud.

ESP32 -> PC
    KEY:<char>              e.g. KEY:1, KEY:#
    MACRO:<action>          legacy action name (host app ignores it, PS1 uses it)
    ENC1:<pos> / ENC2:<pos> absolute encoder position
    ENC1_SW:1 / ENC2_SW:1   encoder button press
    PROFILE:<n>:<name>      active page/profile changed (1 based)
    DISPLAY:<text>          echo of H: test text

PC -> ESP32
    T:CPU=..;GPU=..;RAM=..;TEMP=.. telemetry for on-device display (1 Hz)
    P:<n>                   switch page (1..3)
    N:<n>:<name>            set page name shown on the display
    H:<text>                show test text for 5 s
    L:<...>                 reserved for RGB LED control (hardware not present yet)
    W:B:<bytes>             wallpaper transfer begin (raw big-endian RGB565)
    W:<idx>:<base64>        wallpaper chunk, 48 raw bytes -> 64 base64 chars
    W:E                     wallpaper transfer end -> display + redraw
"""

import base64

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
        return ("key", {"char": ch, "row": rc[0] if rc else -1, "col": rc[1] if rc else -1})

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


# 48 raw bytes -> exactly 64 base64 chars; longest line "W:899:<64>\n" = 72 < 96.
WP_CHUNK_BYTES = 48


def wallpaper_transfer_lines(raw):
    """Yield '\n'-terminated lines transferring an RGB565 wallpaper blob."""
    yield "W:B:%d\n" % len(raw)
    for index in range(0, len(raw), WP_CHUNK_BYTES):
        chunk = base64.b64encode(raw[index:index + WP_CHUNK_BYTES])
        yield "W:%d:%s\n" % (index // WP_CHUNK_BYTES, chunk.decode("ascii"))
    yield "W:E\n"
