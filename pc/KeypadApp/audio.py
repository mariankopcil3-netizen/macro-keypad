"""Windows audio control via pycaw (Core Audio).

Master volume of the default output device and per-application volume
(audio sessions), used by the encoders:

    ENC1 / ENC1_SW -> master volume / master mute
    ENC2 / ENC2_SW -> volume / mute of one target application

All calls fetch a fresh COM object, so they survive apps starting/stopping
and device changes. Thread-safe: COM is initialised per calling thread, so the
background poller thread and the Qt main thread can both use it.
"""

import os
import threading

import psutil

try:
    import comtypes
    from pycaw.utils import AudioUtilities
except ImportError:  # optional dependency, app still runs without audio
    comtypes = None
    AudioUtilities = None

AUDIO_SESSION_STATE_ACTIVE = 1

_com_lock = threading.Lock()
_com_local = threading.local()


def available():
    return AudioUtilities is not None


def _ensure_com():
    # COM must be initialised once *per thread* (poller thread + GUI thread).
    if comtypes is None or getattr(_com_local, "ready", False):
        return
    with _com_lock:
        if getattr(_com_local, "ready", False):
            return
        try:
            comtypes.CoInitialize()
        except OSError:
            pass
        _com_local.ready = True


def _require():
    if AudioUtilities is None:
        raise RuntimeError("pycaw is not installed (pip install -r requirements.txt)")
    _ensure_com()


def _device():
    _require()
    device = AudioUtilities.GetSpeakers()
    if device is None:
        raise RuntimeError("no audio output device")
    return device


def _normalize(name):
    base = os.path.basename((name or "").strip()).lower()
    if base.endswith(".exe"):
        base = base[:-4]
    return base


# ------------------------------------------------------------------ master
def master_percent():
    return int(round(_device().volume_percent))


def set_master_percent(percent):
    device = _device()
    device.volume_percent = max(0, min(100, int(percent)))
    return int(round(device.volume_percent))


def master_muted():
    return bool(_device().EndpointVolume.GetMute())


def set_master_muted(muted):
    _device().EndpointVolume.SetMute(bool(muted), None)


def toggle_master_mute():
    device = _device()
    state = not bool(device.EndpointVolume.GetMute())
    device.EndpointVolume.SetMute(state, None)
    return state


# --------------------------------------------------------------- per app
def snapshot(target_process=None):
    """One COM round-trip: master + optional app state for the UI.

    Returns {"master", "master_muted", "app", "app_muted", "app_name"}.
    Never raises; missing values are None.
    """
    result = {"master": None, "master_muted": False, "app": None,
              "app_muted": False, "app_name": target_process or ""}
    if AudioUtilities is None:
        return result
    try:
        device = _device()
        result["master"] = int(round(device.volume_percent))
        result["master_muted"] = bool(device.EndpointVolume.GetMute())
    except Exception:  # noqa: BLE001
        return result
    if target_process:
        try:
            session = _pick_session(target_process)
            if session is not None:
                result["app"] = int(round(
                    session.SimpleAudioVolume.GetMasterVolume() * 100))
                result["app_muted"] = bool(session.SimpleAudioVolume.GetMute())
        except Exception:  # noqa: BLE001
            pass
    return result


def _sessions_for(process_name):
    _require()
    wanted = _normalize(process_name)
    if not wanted:
        return []
    matches = []
    for session in AudioUtilities.GetAllSessions():
        process = session.Process
        if process is None:
            continue
        try:
            name = _normalize(process.name())
        except (psutil.Error, OSError):
            continue
        if name == wanted:
            matches.append(session)
    return matches


def _pick_session(process_name):
    sessions = _sessions_for(process_name)
    for session in sessions:
        try:
            if session.State == AUDIO_SESSION_STATE_ACTIVE:
                return session
        except Exception:  # noqa: BLE001 - stale session, try the next one
            continue
    return sessions[0] if sessions else None


def app_volume_percent(process_name):
    session = _pick_session(process_name)
    if session is None:
        return None
    return int(round(session.SimpleAudioVolume.GetMasterVolume() * 100))


def set_app_volume_percent(process_name, percent):
    session = _pick_session(process_name)
    if session is None:
        return None
    value = max(0, min(100, int(percent))) / 100.0
    session.SimpleAudioVolume.SetMasterVolume(value, None)
    return int(round(session.SimpleAudioVolume.GetMasterVolume() * 100))


def app_muted(process_name):
    session = _pick_session(process_name)
    if session is None:
        return None
    return bool(session.SimpleAudioVolume.GetMute())


def toggle_app_mute(process_name):
    session = _pick_session(process_name)
    if session is None:
        return None
    muted = not bool(session.SimpleAudioVolume.GetMute())
    session.SimpleAudioVolume.SetMute(muted, None)
    return muted


def audio_apps():
    """Process names that currently own an audio session (e.g. Spotify.exe)."""
    _require()
    names = set()
    for session in AudioUtilities.GetAllSessions():
        process = session.Process
        if process is None:
            continue
        try:
            names.add(process.name())
        except (psutil.Error, OSError):
            continue
    return sorted(names, key=str.lower)


def status_text(target=None):
    """One-line summary for the UI (never raises)."""
    parts = []
    try:
        suffix = " (mute)" if master_muted() else ""
        parts.append("master %d%%%s" % (master_percent(), suffix))
    except Exception as exc:  # noqa: BLE001
        return "audio: %s" % exc
    if target:
        label = os.path.basename(target)
        try:
            volume = app_volume_percent(target)
            if volume is None:
                parts.append("%s: not playing" % label)
            else:
                suffix = " (mute)" if app_muted(target) else ""
                parts.append("%s %d%%%s" % (label, volume, suffix))
        except Exception as exc:  # noqa: BLE001
            parts.append("%s: %s" % (label, exc))
    return " | ".join(parts)
