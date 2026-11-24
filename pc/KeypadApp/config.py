"""Binding configuration (bindings.json).

A binding is a small dict, one per key. Supported action types:

    {"type": "app",    "target": "spotify:" | "C:/path/app.exe",
                       "mode": "auto" | "always" | "focus",
                       "process": "Spotify.exe"}          # process optional
    {"type": "hotkey", "combo": "Ctrl+Shift+S"}
    {"type": "media",  "code": "play_pause" | "stop" | "next" | "prev"
                               | "vol_up" | "vol_down" | "mute"}
    {"type": "text",   "text": "hello"}
    {"type": "page",   "page": 2}
    {"type": "none"}
"""

import copy
import json
import os
import re
from pathlib import Path

CONFIG_PATH = Path(__file__).with_name("bindings.json")
DEFAULT_BAUD = 115200
_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _app(target, mode="auto", process=None):
    action = {"type": "app", "target": target, "mode": mode}
    if process:
        action["process"] = process
    return action


def _hotkey(combo):
    return {"type": "hotkey", "combo": combo}


def _media(code):
    return {"type": "media", "code": code}


def _text(text):
    return {"type": "text", "text": text}


def _page(n):
    return {"type": "page", "page": n}


def _none():
    return {"type": "none"}


DEFAULT_CONFIG = {
    "version": 1,
    "baud": DEFAULT_BAUD,
    "last_port": "",
    "accent": "#A855F7",
    "terminal_visible": False,
    "show_unknown_sources": False,
    "audio": {
        "volume_step": 2,
        "target_app": "Spotify.exe",
    },
    "encoder": {
        "e2_mode": "app_volume",
        "e2_combo": "Ctrl+Shift+M",
    },
    "profiles": [
        {
            "name": "EDIT",
            "keys": {
                "1": _app("spotify:"),
                "2": _hotkey("Ctrl+Y"),
                "3": _hotkey("Ctrl+S"),
                "4": _hotkey("Ctrl+C"),
                "5": _hotkey("Ctrl+X"),
                "6": _hotkey("Ctrl+V"),
                "7": _hotkey("Ctrl+F"),
                "8": _hotkey("Ctrl+H"),
                "9": _hotkey("Ctrl+A"),
                "*": _hotkey("Alt+F4"),
                "0": _hotkey("Delete"),
                "#": _hotkey("Enter"),
            },
        },
        {
            "name": "MEDIA",
            "keys": {
                "1": _media("play_pause"),
                "2": _media("stop"),
                "3": _media("next"),
                "4": _media("prev"),
                "5": _media("vol_up"),
                "6": _media("vol_down"),
                "7": _media("mute"),
                "8": _hotkey("Ctrl+Left"),
                "9": _hotkey("Ctrl+Right"),
                "*": _hotkey("F11"),
                "0": _hotkey("V"),
                "#": _hotkey("Shift+S"),
            },
        },
        {
            "name": "CODE",
            "keys": {
                "1": _hotkey("Ctrl+Shift+B"),
                "2": _hotkey("F5"),
                "3": _hotkey("F5"),
                "4": _hotkey("F10"),
                "5": _hotkey("F11"),
                "6": _hotkey("Shift+F11"),
                "7": _hotkey("F9"),
                "8": _hotkey("F5"),
                "9": _hotkey("Shift+F5"),
                "*": _hotkey("Ctrl+`"),
                "0": _hotkey("Shift+Alt+F"),
                "#": _hotkey("Ctrl+Enter"),
            },
        },
    ],
}


def default_config():
    return copy.deepcopy(DEFAULT_CONFIG)


def _normalize(cfg):
    """Fill missing fields so an older/hand-edited file keeps working."""
    if not isinstance(cfg, dict):
        return default_config()
    cfg.setdefault("version", 1)
    cfg.setdefault("baud", DEFAULT_BAUD)
    cfg.setdefault("last_port", "")
    accent = cfg.get("accent")
    if not isinstance(accent, str) or not _HEX_RE.match(accent):
        cfg["accent"] = DEFAULT_CONFIG["accent"]
    else:
        cfg["accent"] = accent.lower()
    cfg["terminal_visible"] = bool(cfg.get("terminal_visible", False))
    cfg["show_unknown_sources"] = bool(cfg.get("show_unknown_sources", False))
    audio = cfg.get("audio")
    if not isinstance(audio, dict):
        cfg["audio"] = copy.deepcopy(DEFAULT_CONFIG["audio"])
    else:
        audio.setdefault("volume_step", DEFAULT_CONFIG["audio"]["volume_step"])
        audio.setdefault("target_app", DEFAULT_CONFIG["audio"]["target_app"])
    encoder = cfg.get("encoder")
    if not isinstance(encoder, dict):
        encoder = copy.deepcopy(DEFAULT_CONFIG["encoder"])
        cfg["encoder"] = encoder
    else:
        encoder.setdefault("e2_mode", DEFAULT_CONFIG["encoder"]["e2_mode"])
        encoder.setdefault("e2_combo", DEFAULT_CONFIG["encoder"]["e2_combo"])
    if encoder.get("e2_mode") not in ("app_volume", "scroll", "zoom", "hotkey"):
        encoder["e2_mode"] = DEFAULT_CONFIG["encoder"]["e2_mode"]
    profiles = cfg.get("profiles")
    if not isinstance(profiles, list) or not profiles:
        cfg["profiles"] = default_config()["profiles"]
        return cfg

    defaults = DEFAULT_CONFIG["profiles"]
    while len(cfg["profiles"]) < len(defaults):
        idx = len(cfg["profiles"])
        cfg["profiles"].append(copy.deepcopy(defaults[idx]))
    for idx, profile in enumerate(cfg["profiles"]):
        if not isinstance(profile, dict):
            cfg["profiles"][idx] = copy.deepcopy(defaults[min(idx, len(defaults) - 1)])
            continue
        profile.setdefault("name", defaults[min(idx, len(defaults) - 1)]["name"])
        keys = profile.get("keys")
        if not isinstance(keys, dict):
            profile["keys"] = {}
        for ch in "123456789*0#":
            keys.setdefault(ch, _none())
    return cfg


def load_config(path=CONFIG_PATH):
    path = Path(path)
    if not path.exists():
        cfg = default_config()
        save_config(cfg, path)
        return cfg
    try:
        with path.open("r", encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, ValueError):
        return default_config()
    return _normalize(cfg)


def save_config(cfg, path=CONFIG_PATH):
    path = Path(path)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


_TARGET_EXTENSIONS = (".exe", ".lnk", ".bat", ".cmd", ".msc", ".url", ".com")


def _strip_exe(name):
    lowered = (name or "").lower()
    for extension in _TARGET_EXTENSIONS:
        if lowered.endswith(extension):
            return name[: -len(extension)]
    return name or ""


def _label_from_target(target):
    """Readable app name from a launch target (path, URI or AUMID)."""
    text = str(target or "").strip().strip('"')
    if not text:
        return ""
    lowered = text.lower()
    if lowered.startswith("shell:appsfolder\\"):
        family = text.split("\\", 1)[1].split("!", 1)[0].split("_", 1)[0]
        return family.rsplit(".", 1)[-1]
    if "://" in text:
        host = text.split("://", 1)[1].split("/", 1)[0]
        return host[4:] if host.lower().startswith("www.") else host
    if "\\" in text or "/" in text:
        base = text.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
        return _strip_exe(base)
    if text.endswith(":"):
        scheme = text[:-1]
        return scheme[:1].upper() + scheme[1:] if scheme else ""
    return _strip_exe(text)


def get_display_label(binding):
    """Universal key label for any action type; never returns an empty string."""
    binding = binding or {}
    for field in ("label", "title", "name"):
        value = str(binding.get(field) or "").strip()
        if value:
            return value
    kind = binding.get("type", "none")
    if kind == "app":
        label = _label_from_target(binding.get("target"))
        if not label or "_" in label or label.startswith("Microsoft."):
            process = os.path.basename(str(binding.get("process") or "")
                                       .replace("\\", "/"))
            label = _strip_exe(process) or label
        return label or "App"
    if kind == "hotkey":
        return str(binding.get("combo") or "").strip() or "Hotkey"
    if kind == "media":
        code = str(binding.get("code") or "").strip()
        return code.replace("_", " ") or "Media"
    if kind == "text":
        return str(binding.get("text") or "").strip() or "Text"
    if kind == "page":
        return "Page %s" % binding.get("page", "?")
    for field in ("target", "value", "combo", "code", "text"):
        value = str(binding.get(field) or "").strip()
        if value:
            return _label_from_target(value) or value
    return "---"


def action_summary(action):
    """Short human readable label used on the keypad grid buttons."""
    kind = (action or {}).get("type", "none")
    if kind == "app":
        target = action.get("target", "")
        mode = action.get("mode", "auto")
        return "app: %s (%s)" % (target, mode)
    if kind == "hotkey":
        return action.get("combo", "")
    if kind == "media":
        return "media: %s" % action.get("code", "")
    if kind == "text":
        return "text: %s" % action.get("text", "")
    if kind == "page":
        return "page %s" % action.get("page", "?")
    return "---"
