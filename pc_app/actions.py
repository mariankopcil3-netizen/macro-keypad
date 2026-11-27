"""Action engine: launches apps, sends hotkeys / media keys / text on Windows.

Only stdlib ctypes is used, no extra dependencies.
"""

import ctypes
import ctypes.wintypes as wt
import os
import re
import subprocess
import time

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SW_RESTORE = 9

VK_NAMES = {
    "CTRL": 0x11, "CONTROL": 0x11, "SHIFT": 0x10, "ALT": 0x12, "WIN": 0x5B,
    "ENTER": 0x0D, "RETURN": 0x0D, "ESC": 0x1B, "ESCAPE": 0x1B,
    "TAB": 0x09, "SPACE": 0x20, "BACKSPACE": 0x08, "DELETE": 0x2E,
    "INSERT": 0x2D, "UP": 0x26, "DOWN": 0x28, "LEFT": 0x25, "RIGHT": 0x27,
    "HOME": 0x24, "END": 0x23, "PAGEUP": 0x21, "PAGEDOWN": 0x22,
    "`": 0xC0, "-": 0xBD, "=": 0xBB, "[": 0xDB, "]": 0xDD,
    ";": 0xBA, "'": 0xDE, ",": 0xBC, ".": 0xBE, "/": 0xBF, "\\": 0xDC,
}
for _i in range(1, 25):
    VK_NAMES["F%d" % _i] = 0x6F + _i

MEDIA_VK = {
    "play_pause": 0xB3, "stop": 0xB2, "next": 0xB0, "prev": 0xB1,
    "vol_up": 0xAF, "vol_down": 0xAE, "mute": 0xAD,
}

URI_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:")
DRIVE_RE = re.compile(r"^[a-zA-Z]:[\\/]")

KNOWN_PROCESSES = {
    "spotify": "Spotify.exe",
    "discord": "Discord.exe",
    "steam": "steam.exe",
    "code": "Code.exe",
    "vscode": "Code.exe",
    "notepad": "notepad.exe",
    "chrome": "chrome.exe",
    "firefox": "firefox.exe",
    "explorer": "explorer.exe",
    "terminal": "WindowsTerminal.exe",
}


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wt.WORD),
        ("wScan", wt.WORD),
        ("dwFlags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("u", _INPUTUNION)]


user32.SendInput.argtypes = (wt.UINT, ctypes.POINTER(_INPUT), ctypes.c_int)
user32.SendInput.restype = wt.UINT


def vk_for(name):
    key = name.strip().upper()
    if key in VK_NAMES:
        return VK_NAMES[key]
    if len(key) == 1 and key.isalnum():
        return ord(key)
    return None


def _key_event(vk, up=False):
    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP if up else 0, 0)


def send_hotkey(combo):
    """Press a key combination such as 'Ctrl+Shift+S' in the focused window."""
    parts = [p for p in (part.strip() for part in combo.split("+")) if p]
    if not parts:
        raise ValueError("empty hotkey combo")
    vks = []
    for part in parts:
        vk = vk_for(part)
        if vk is None:
            raise ValueError("unknown key '%s' in combo '%s'" % (part, combo))
        vks.append(vk)
    for vk in vks:
        _key_event(vk)
        time.sleep(0.01)
    for vk in reversed(vks):
        _key_event(vk, up=True)
        time.sleep(0.01)


MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040


def send_wheel(steps, ctrl=False):
    """Scroll the mouse wheel by `steps` detents (optionally holding Ctrl)."""
    steps = int(steps)
    if not steps:
        return
    if ctrl:
        _key_event(VK_NAMES["CTRL"])
        time.sleep(0.01)
    user32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, steps * 120, 0)
    if ctrl:
        time.sleep(0.01)
        _key_event(VK_NAMES["CTRL"], up=True)


def send_middle_click():
    """Middle mouse button click at the current cursor position."""
    user32.mouse_event(MOUSEEVENTF_MIDDLEDOWN, 0, 0, 0, 0)
    time.sleep(0.01)
    user32.mouse_event(MOUSEEVENTF_MIDDLEUP, 0, 0, 0, 0)


def send_text(text):
    """Type a string into the focused window (unicode safe)."""
    for ch in text:
        for up in (False, True):
            inp = _INPUT()
            inp.type = INPUT_KEYBOARD
            inp.u.ki.wVk = 0
            inp.u.ki.wScan = ord(ch)
            inp.u.ki.dwFlags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0)
            inp.u.ki.time = 0
            inp.u.ki.dwExtraInfo = None
            user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))


def send_media(code):
    """Send a media key (play_pause, next, vol_up, mute, ...)."""
    vk = MEDIA_VK.get(code)
    if vk is None:
        raise ValueError("unknown media code '%s'" % code)
    _key_event(vk)
    _key_event(vk, up=True)


def volume_steps(steps, max_steps=5):
    if not steps:
        return 0
    code = "vol_up" if steps > 0 else "vol_down"
    count = min(abs(int(steps)), max_steps)
    for _ in range(count):
        send_media(code)
        time.sleep(0.03)
    return count


def _process_exe(pid):
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wt.DWORD(len(buf))
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value
        return None
    finally:
        kernel32.CloseHandle(handle)


_ENUM_PROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


def focus_window(process_name):
    """Bring the first visible window of process_name to the foreground."""
    target = os.path.basename(process_name).lower()
    if not target.endswith(".exe"):
        target += ".exe"
    found = []

    @_ENUM_PROC
    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        exe = _process_exe(pid.value)
        if exe and os.path.basename(exe).lower() == target:
            found.append(hwnd)
            return False
        return True

    user32.EnumWindows(callback, 0)
    if not found:
        return False
    hwnd = found[0]
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    user32.SetForegroundWindow(hwnd)
    return True


def resolve_process(target, explicit=None):
    if explicit:
        return explicit
    if DRIVE_RE.match(target) or target.lower().endswith(".exe"):
        return os.path.basename(target)
    match = URI_RE.match(target)
    if match:
        scheme = target.split(":", 1)[0].lower()
        return KNOWN_PROCESSES.get(scheme, scheme + ".exe")
    return os.path.basename(target)


def start_target(target):
    lowered = target.lower()
    if lowered.startswith("shell:"):
        subprocess.Popen(["explorer.exe", target])
    elif DRIVE_RE.match(target) or lowered.endswith(".exe") or os.path.exists(target):
        subprocess.Popen([target])
    else:
        os.startfile(target)


def launch_app(target, mode="auto", process=None, log=print):
    """Start or focus an app.

    mode: auto   -> focus if already running, otherwise start
          always -> always start a new instance
          focus  -> only focus, never start
    """
    process = resolve_process(target, process)
    if mode in ("auto", "focus") and process and focus_window(process):
        log("focused %s" % process)
        return True
    if mode == "focus":
        log("%s is not running (focus only)" % (process or target))
        return False
    start_target(target)
    log("started %s" % target)
    return True


def execute_action(action, ctx):
    """Run one binding. ctx provides log(), send(), set_page() and dry_run."""
    action = action or {}
    kind = action.get("type", "none")
    dry = getattr(ctx, "dry_run", False)

    try:
        if kind == "app":
            target = action.get("target", "")
            mode = action.get("mode", "auto")
            process = action.get("process")
            if not target:
                ctx.log("app binding has no target")
                return False
            if dry:
                ctx.log("[dry] launch %s (%s, process=%s)" % (target, mode, process or "auto"))
                return True
            return launch_app(target, mode, process, log=ctx.log)

        if kind == "hotkey":
            combo = action.get("combo", "")
            if not combo:
                ctx.log("hotkey binding has no combo")
                return False
            if dry:
                ctx.log("[dry] hotkey %s" % combo)
                return True
            send_hotkey(combo)
            ctx.log("hotkey %s" % combo)
            return True

        if kind == "media":
            code = action.get("code", "")
            if dry:
                ctx.log("[dry] media %s" % code)
                return True
            send_media(code)
            ctx.log("media %s" % code)
            return True

        if kind == "text":
            text = action.get("text", "")
            if dry:
                ctx.log("[dry] text %r" % text)
                return True
            send_text(text)
            ctx.log("typed %r" % text)
            return True

        if kind == "page":
            page = int(action.get("page", 1))
            if dry:
                ctx.log("[dry] switch page %d" % page)
                return True
            ctx.set_page(page)
            return True

        if kind == "none":
            ctx.log("no binding")
            return False

        ctx.log("unknown action type '%s'" % kind)
        return False
    except Exception as exc:  # noqa: BLE001 - surface any failure in the log
        ctx.log("action failed: %s" % exc)
        return False
