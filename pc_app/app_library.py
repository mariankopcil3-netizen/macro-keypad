"""Windows application library: scans Start Menu shortcuts, the Uninstall
registry and running processes, resolves icons from .exe files and exposes a
searchable list widget that supports click-to-assign and drag & drop."""

import json
import os
import re
import subprocess
import threading
import winreg
from pathlib import Path

import psutil
from PySide6.QtCore import (QFileInfo, QMimeData, QPoint, QPointF, QRectF,
                            QSize, Qt, QTimer, Signal)
from PySide6.QtGui import (QColor, QDrag, QFont, QFontMetrics, QIcon,
                           QPainter, QPainterPath, QPen, QPixmap)
from PySide6.QtWidgets import (QAbstractItemView, QFileIconProvider, QFrame,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QStyle, QStyledItemDelegate,
                               QVBoxLayout)

import theme
from widgets import MIME_ENTRY, SegmentedControl

_ICON_PROVIDER = None
_ICON_CACHE = {}
_SHELL_LOCAL = threading.local()
_SHELL_BROKEN = False
_LNK_CACHE = {}

AUX_EXE_TOKENS = (
    "helper", "service", "crashpad", "crashhandler", "crash", "updater",
    "uninstall", "unins", "setup", "install", "redist", "daemon", "console",
    "server", "cmd", "help", "manual", "readme", "update", "diagnostic",
    "broker", "host", "webview", "runtime", "cc_engine", "redeem", "oalinst",
    "git-bash",
)
SKIP_DIRS = {
    "bin", "lib", "resources", "node_modules", "site-packages", "scripts",
    "venv", ".venv", "locales", "plugins", "extensions",
}
SKIP_PROFILE_DIRS = {
    "temp", "tmp", "cache", "caches", "crashdumps", "d3dscache", "packages",
    "microsoft", "connecteddevicesplatform", "comms", "nvidia",
    "virtualstore", "application data", "history", "inetcookies",
}
SYSTEM_EXE_NAMES = {
    "wmpnetwk", "cncmd", "cmd", "conhost", "svchost", "dllhost", "explorer",
    "runtimebroker", "searchhost", "shellexperiencehost",
    "startmenuexperiencehost", "textinputhost", "applicationframehost",
    "ctfmon", "rundll32", "regsvr32", "tabtip", "wsl", "mshta",
}
SYSTEM_PATH_FRAGMENTS = (
    "\\programdata\\microsoft\\windows defender",
    "\\program files\\common files\\microsoft shared\\ink",
    "\\windows media player\\",
    "\\internet explorer\\",
    "\\redists\\",
    "\\administrative tools\\",
    "\\n\u00e1stroje pro spr\u00e1vu\\",
    "\\windows system\\",
    "\\windows accessories\\",
    "\\systemapps\\",
    "\\package cache\\",
    "\\installer\\",
    "\\temp\\",
    "\\pending\\",
    "\\updates\\",
    "\\meta horizon\\support\\",
)
AUX_SYSTEM_STEMS = {
    "python", "pythonw", "python3", "node", "npm", "npx", "java", "javaw",
    "javaws", "javacpl", "bash", "sh", "cscript", "wscript", "wsl", "wt",
    "winget", "msiexec", "msedgewebview2",
}
GROUP_STRIP = {
    "bin", "apps", "app", "resources", "modules", "plugins", "engine",
    "moments", "sonar", "cvgamesense", "prism", "x64", "x86", "win64",
    "win32", "amd64", "arm64", "redists", "current", "latest", "runtime",
    "system_tray", "commonredist", "pending", "updates", "oculus-runtime",
}
GROUP_STRIP_RE = re.compile(r"^(?:v?\d+(?:\.\d+)+|x64[_-].*|x86[_-].*|"
                            r"win(?:32|64)[_-].*|app-\d.*)$", re.I)
NAME_VERSION_RE = re.compile(r"\s+v?\d+(?:\.\d+)+\s*$", re.I)
SPOTIFY_URI = "spotify:"


def icon_for_exe(path):
    global _ICON_PROVIDER
    if not path:
        return None
    path = os.path.abspath(path)
    if path in _ICON_CACHE:
        return _ICON_CACHE[path]
    icon = None
    try:
        if os.path.isfile(path):
            if _ICON_PROVIDER is None:
                _ICON_PROVIDER = QFileIconProvider()
            candidate = _ICON_PROVIDER.icon(QFileInfo(path))
            if not candidate.isNull():
                icon = candidate
    except Exception:  # noqa: BLE001 - icon is cosmetic
        icon = None
    _ICON_CACHE[path] = icon
    return icon


def _shell():
    """WScript.Shell is apartment-bound: one instance per worker thread."""
    global _SHELL_BROKEN
    shell = getattr(_SHELL_LOCAL, "shell", None)
    if shell is not None or _SHELL_BROKEN:
        return shell
    try:
        import comtypes
        import comtypes.client
        try:
            comtypes.CoInitialize()
        except OSError:
            pass
        shell = comtypes.client.CreateObject("WScript.Shell", dynamic=True)
    except Exception:  # noqa: BLE001
        _SHELL_BROKEN = True
        shell = None
    _SHELL_LOCAL.shell = shell
    return shell


def _resolve_lnk(path):
    try:
        key = (str(path), Path(path).stat().st_mtime)
    except OSError:
        key = (str(path), 0)
    if key in _LNK_CACHE:
        return _LNK_CACHE[key]
    shell = _shell()
    target = None
    if shell is not None:
        try:
            target = shell.CreateShortcut(str(path)).TargetPath or None
        except Exception:  # noqa: BLE001
            target = None
    _LNK_CACHE[key] = target
    return target


def _clean_icon_path(raw):
    raw = (raw or "").strip()
    if not raw:
        return ""
    if raw.startswith('"'):
        end = raw.find('"', 1)
        return raw[1:end] if end > 0 else raw.strip('"')
    return raw.split(",")[0].strip().strip('"')


def _registry_apps():
    entries = []
    locations = (
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
         winreg.KEY_READ | winreg.KEY_WOW64_64KEY),
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
         winreg.KEY_READ | winreg.KEY_WOW64_32KEY),
        (winreg.HKEY_CURRENT_USER,
         r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
         winreg.KEY_READ),
    )
    for hive, subkey, access in locations:
        try:
            root = winreg.OpenKey(hive, subkey, 0, access)
        except OSError:
            continue
        try:
            count = winreg.QueryInfoKey(root)[0]
            for index in range(count):
                try:
                    name = winreg.EnumKey(root, index)
                    with winreg.OpenKey(root, name) as key:
                        try:
                            display = winreg.QueryValueEx(key, "DisplayName")[0]
                        except OSError:
                            continue
                        try:
                            if winreg.QueryValueEx(key, "SystemComponent")[0] == 1:
                                continue
                        except OSError:
                            pass
                        exe = ""
                        try:
                            exe = _clean_icon_path(
                                winreg.QueryValueEx(key, "DisplayIcon")[0])
                        except OSError:
                            pass
                        if not exe or not exe.lower().endswith(".exe"):
                            try:
                                location = winreg.QueryValueEx(key, "InstallLocation")[0]
                            except OSError:
                                location = ""
                            exe = os.path.join(location, "") if location else ""
                        if not exe or not os.path.isfile(exe):
                            continue
                        entries.append({"name": str(display), "exe": exe,
                                        "kind": "app", "registry": True})
                except OSError:
                    continue
        finally:
            root.Close()
    return entries


def _app_paths():
    """EXE targets registered under App Paths (HKCU + HKLM, both widths)."""
    entries = []
    locations = (
        (winreg.HKEY_CURRENT_USER,
         r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths",
         winreg.KEY_READ),
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths",
         winreg.KEY_READ | winreg.KEY_WOW64_64KEY),
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths",
         winreg.KEY_READ | winreg.KEY_WOW64_32KEY),
    )
    for hive, subkey, access in locations:
        try:
            root = winreg.OpenKey(hive, subkey, 0, access)
        except OSError:
            continue
        try:
            count = winreg.QueryInfoKey(root)[0]
            for index in range(count):
                try:
                    name = winreg.EnumKey(root, index)
                    with winreg.OpenKey(root, name) as key:
                        try:
                            exe = _clean_icon_path(winreg.QueryValueEx(key, "")[0])
                        except OSError:
                            continue
                        if not exe or not exe.lower().endswith(".exe"):
                            continue
                        if not os.path.isfile(exe):
                            continue
                        label = os.path.splitext(name)[0]
                        entries.append({"name": label, "exe": exe, "kind": "app",
                                        "registry": True})
                except OSError:
                    continue
        finally:
            root.Close()
    return entries


def _start_menu_links():
    entries = []
    roots = []
    program_data = os.environ.get("ProgramData")
    app_data = os.environ.get("APPDATA")
    if program_data:
        roots.append(Path(program_data) / "Microsoft/Windows/Start Menu/Programs")
    if app_data:
        roots.append(Path(app_data) / "Microsoft/Windows/Start Menu/Programs")
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*.lnk"):
            target = _resolve_lnk(path)
            if not target or not target.lower().endswith(".exe"):
                continue
            if not os.path.isfile(target):
                continue
            entries.append({"name": path.stem, "exe": target, "kind": "app",
                            "menu": True})
    return entries


def _running_apps():
    entries = []
    for proc in psutil.process_iter(["name", "exe"]):
        try:
            exe = proc.info.get("exe") or ""
        except (psutil.Error, OSError):
            continue
        if not exe or not exe.lower().endswith(".exe"):
            continue
        if not os.path.isfile(exe):
            continue
        name = proc.info.get("name") or os.path.basename(exe)
        entries.append({"name": os.path.splitext(name)[0], "exe": exe,
                        "kind": "app", "running": True})
    return entries


def _walk_programs(root, max_depth=3, skip_dirs=SKIP_DIRS):
    root = Path(root)
    for current, dirs, files in os.walk(root):
        try:
            depth = len(Path(current).relative_to(root).parts)
        except ValueError:
            depth = max_depth
        if depth >= max_depth:
            dirs[:] = []
        else:
            dirs[:] = [d for d in dirs if d.lower() not in skip_dirs
                       and not d.startswith(".")]
        for name in files:
            if name.lower().endswith(".exe"):
                yield Path(current) / name


def _user_install_apps():
    """Per-user installs: %LOCALAPPDATA%\\Programs, %LOCALAPPDATA% and
    %APPDATA% (shallow), plus WindowsApps aliases."""
    entries = []
    local = os.environ.get("LOCALAPPDATA")
    appdata = os.environ.get("APPDATA")
    roots = []
    if local:
        programs = Path(local) / "Programs"
        if programs.is_dir():
            roots.append((programs, 3, SKIP_DIRS))
        local_root = Path(local)
        if local_root.is_dir():
            roots.append((local_root, 2, SKIP_DIRS | SKIP_PROFILE_DIRS))
    if appdata:
        roaming = Path(appdata)
        if roaming.is_dir():
            roots.append((roaming, 2, SKIP_DIRS | SKIP_PROFILE_DIRS))
    for root, depth, skip in roots:
        for path in _walk_programs(root, max_depth=depth, skip_dirs=skip):
            if path.is_file():
                entries.append({"name": path.stem, "exe": str(path),
                                "kind": "app"})
    if local:
        aliases = Path(local) / "Microsoft" / "WindowsApps"
        if aliases.is_dir():
            for path in aliases.glob("*.exe"):
                if path.is_file():
                    entries.append({"name": path.stem, "exe": str(path),
                                    "kind": "app", "alias": True})
    return entries


_STORE_LOGO_CACHE = None


def _rank_logo(path):
    """Sort key for a candidate package logo (lower = better), None = reject."""
    name = os.path.basename(path).lower()
    if not name.endswith(".png"):
        return None
    if "contrast" in name or "wide" in name:
        return None
    if "logo" not in name and "applist" not in name:
        return None
    scale = 0
    match = re.search(r"scale-(\d+)", name)
    if match:
        scale = int(match.group(1))
    if "targetsize" in name:
        scale -= 5000
    if "square44" in name:
        pref = 0
    elif "applist" in name:
        pref = 1
    elif "square150" in name:
        pref = 2
    elif "storelogo" in name:
        pref = 3
    else:
        pref = 4
    return (-scale, pref, name)


def _store_logo_map():
    """PackageFamilyName -> best square logo png (one cached pass over Appx)."""
    global _STORE_LOGO_CACHE
    if _STORE_LOGO_CACHE is not None:
        return _STORE_LOGO_CACHE
    command = (
        "[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
        "$out=@{};"
        "Get-AppxPackage -ErrorAction SilentlyContinue | ForEach-Object {"
        "if($_.IsFramework -or $_.IsResourcePackage -or -not $_.InstallLocation){return};"
        "$f=Get-ChildItem -LiteralPath $_.InstallLocation -Recurse -Depth 2 -Filter *.png "
        "-File -ErrorAction SilentlyContinue | "
        "Where-Object { $_.Name -match '(?i)(logo|applist)' -and "
        "$_.Name -notmatch '(?i)(contrast|wide)' } | Select-Object -ExpandProperty FullName;"
        "if($f){$out[$_.PackageFamilyName]=@($f)}};"
        "ConvertTo-Json -InputObject $out -Compress"
    )
    logos = {}
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=45, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        _STORE_LOGO_CACHE = logos
        return logos
    if result.returncode == 0 and (result.stdout or "").strip():
        try:
            rows = json.loads(result.stdout)
        except ValueError:
            rows = None
        if isinstance(rows, dict):
            for family, paths in rows.items():
                best, best_rank = None, None
                for path in paths or []:
                    if not isinstance(path, str):
                        continue
                    rank = _rank_logo(path)
                    if rank is None:
                        continue
                    if best_rank is None or rank < best_rank:
                        best, best_rank = path, rank
                if best and os.path.isfile(best):
                    logos[str(family)] = best
    _STORE_LOGO_CACHE = logos
    return logos


def _store_apps():
    """Microsoft Store / UWP apps from the Start Apps provider."""
    command = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
               "Get-StartApps | Select-Object Name,AppID | ConvertTo-Json -Compress")
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=6, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0 or not (result.stdout or "").strip():
        return []
    try:
        rows = json.loads(result.stdout)
    except ValueError:
        return []
    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, list):
        return []
    entries = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("Name") or "").strip()
        app_id = str(row.get("AppID") or "").strip()
        if not name or not app_id or "!" not in app_id:
            continue
        entry = {
            "name": name, "kind": "app", "glyph": "\u25a6",
            "action": {"type": "app",
                       "target": "shell:AppsFolder\\" + app_id,
                       "mode": "auto"},
        }
        entry["_fam"] = app_id.split("!", 1)[0]
        entries.append(entry)
    if entries:
        logos = _store_logo_map()
        for entry in entries:
            logo = logos.get(entry.pop("_fam", ""))
            if logo and os.path.isfile(logo):
                entry["icon"] = logo
    return entries


def _spotify_logo():
    """Store logo for Spotify (alias exes carry no icon of their own)."""
    for family, path in _store_logo_map().items():
        if family.startswith("SpotifyAB.SpotifyMusic_"):
            return path
    return ""


def _spotify_protocol_registered():
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT,
                            r"spotify\shell\open\command") as key:
            return bool(winreg.QueryValueEx(key, "")[0])
    except OSError:
        return False


def _spotify_entry():
    candidates = []
    app_data = os.environ.get("APPDATA")
    local = os.environ.get("LOCALAPPDATA")
    if app_data:
        candidates.append(os.path.join(app_data, "Spotify", "Spotify.exe"))
    if local:
        candidates.append(os.path.join(local, "Microsoft", "WindowsApps",
                                       "Spotify.exe"))
        candidates.append(os.path.join(local, "Programs", "Spotify", "Spotify.exe"))
    for path in candidates:
        if os.path.isfile(path):
            entry = {
                "name": "Spotify", "exe": path, "kind": "app",
                "action": {"type": "app", "target": path, "mode": "auto",
                           "process": "Spotify.exe", "exe": path},
            }
            if "\\windowsapps\\" in path.lower():
                logo = _spotify_logo()
                if logo:
                    entry["icon"] = logo
            return entry
    if _spotify_protocol_registered():
        entry = {
            "name": "Spotify", "kind": "app", "glyph": "\u266b",
            "action": {"type": "app", "target": SPOTIFY_URI, "mode": "auto",
                       "process": "Spotify.exe"},
        }
        logo = _spotify_logo()
        if logo:
            entry["icon"] = logo
        return entry
    return None


def _stem_is_aux(exe):
    stem = os.path.splitext(os.path.basename(exe or ""))[0].lower()
    if stem in AUX_SYSTEM_STEMS:
        return True
    return any(token in stem for token in AUX_EXE_TOKENS)


def _is_aux_entry(entry):
    exe = (entry.get("exe") or "").lower()
    if exe.endswith(".url"):
        return True
    stem = os.path.splitext(os.path.basename(exe))[0]
    if stem in ("cli", "gui") or stem.endswith(("_cli", "-cli")):
        return True
    return _stem_is_aux(exe)


def _is_system_entry(entry):
    exe = entry.get("exe") or ""
    if not exe:
        return False
    lowered = exe.lower()
    system_root = os.environ.get("SystemRoot", r"C:\Windows").lower().rstrip("\\")
    if lowered.startswith(system_root + "\\"):
        return True
    if "\\windowsapps\\" in lowered or "\\systemapps\\" in lowered:
        return True
    if any(fragment in lowered for fragment in SYSTEM_PATH_FRAGMENTS):
        return True
    stem = os.path.splitext(os.path.basename(lowered))[0]
    return stem in SYSTEM_EXE_NAMES or stem in AUX_SYSTEM_STEMS


def _clean_display_name(entry):
    name = (entry.get("name") or "").strip()
    cleaned = NAME_VERSION_RE.sub("", name).strip()
    return cleaned or name


def _display_rank(entry):
    """Prefer a name equal to the install folder, then a spaced human name,
    then one matching the exe stem; deprioritise versioned names."""
    exe = entry.get("exe") or ""
    stem = os.path.splitext(os.path.basename(exe))[0].lower()
    folder_name = os.path.basename(os.path.dirname(exe).lower())
    name = _clean_display_name(entry).lower()
    return (0 if folder_name and name == folder_name else 1,
            0 if " " in name else 1,
            0 if name == stem else 1,
            1 if NAME_VERSION_RE.search(entry.get("name") or "") else 0,
            len(name))


def _group_key(entry):
    """Install-folder identity: strip module/version leaf dirs so that e.g.
    ...\\SteelSeries\\GG\\apps\\engine and ...\\GG map to the same app."""
    exe = entry.get("exe") or ""
    if not exe or entry.get("alias") or entry.get("action"):
        return "exe:" + exe.lower()
    parts = list(Path(exe).parent.parts)
    while len(parts) > 3:
        leaf = parts[-1]
        if leaf.lower() in GROUP_STRIP or GROUP_STRIP_RE.match(leaf):
            parts.pop()
        else:
            break
    lowered = [part.lower() for part in parts]
    if "appdata" in lowered:
        anchor = lowered.index("appdata") + 1
        if anchor + 1 < len(lowered) and lowered[anchor] in ("roaming", "local") \
                and lowered[anchor + 1] != "programs":
            parts = parts[:anchor + 2]
    return "\\".join(parts).lower()


def _primary_score(entry, group_key):
    exe = entry.get("exe") or ""
    parent = os.path.dirname(exe).lower()
    level = max(0, parent.count("\\") - group_key.count("\\"))
    folder = os.path.basename(parent)
    stem = os.path.splitext(os.path.basename(exe))[0].lower()
    name = _clean_display_name(entry).lower()
    if name == folder:
        match = 0
    elif name == stem:
        match = 1
    elif (stem and stem in name) or (folder and folder in name):
        match = 2
    else:
        match = 3
    return (1 if entry.get("is_hidden") else 0,
            1 if _stem_is_aux(exe) else 0,
            0 if entry.get("menu") or entry.get("registry") else 1,
            level, match, len(name), len(os.path.basename(exe)))


def _pretty_name(entry):
    """Turn bare codename exe stems (wallpaper64, witcher3) into a readable
    name taken from the install folder."""
    name = _clean_display_name(entry)
    exe = entry.get("exe") or ""
    stem = os.path.splitext(os.path.basename(exe))[0]
    if not exe or " " in name or name.lower() != stem.lower():
        return name
    if stem != stem.lower() or not re.search(r"\d", stem):
        return name
    for leaf in reversed(Path(exe).parent.parts):
        low = leaf.lower()
        if low in GROUP_STRIP or GROUP_STRIP_RE.match(leaf):
            continue
        if low in ("users", "appdata", "local", "roaming", "programs"):
            continue
        if " " in leaf or "_" in leaf:
            pretty = leaf.replace("_", " ").strip()
            return pretty.title() if leaf.islower() else pretty
    return name


def _is_profile_noise(entry):
    """Roaming/Local app-data exes without a Start Menu shortcut or an uninstall
    registry record are usually helpers, updaters or leftover installers."""
    exe = (entry.get("exe") or "").lower()
    if not exe or entry.get("menu") or entry.get("registry"):
        return False
    if "\\programs\\" in exe or "\\windowsapps\\" in exe:
        return False
    return any(
        root and exe.startswith(root.lower() + "\\")
        for root in (os.environ.get("APPDATA"), os.environ.get("LOCALAPPDATA"))
    )


def scan_installed_apps(show_unknown=False):
    """Merge registry, Start Menu, per-user installs, App Paths, Store apps and
    running processes. Entries pointing at the same install folder are grouped:
    one primary app stays visible, its modules/helpers become `is_secondary`
    (shown only when `show_unknown` is enabled)."""
    merged = {}
    sources = (
        _registry_apps(), _start_menu_links(), _user_install_apps(),
        _app_paths(), _running_apps(), _store_apps(),
    )
    for source in sources:
        for entry in source:
            if _is_aux_entry(entry) or _is_system_entry(entry) or entry.get("alias"):
                entry["is_hidden"] = True
            key = (entry.get("exe") or "").lower()
            if not key:
                key = "store:" + entry["name"].lower()
            existing = merged.get(key)
            if existing is None:
                merged[key] = entry
                continue
            if _display_rank(entry) < _display_rank(existing):
                existing["name"] = entry["name"]
            if entry.get("running"):
                existing["running"] = True
            if entry.get("is_hidden"):
                existing["is_hidden"] = True
            for flag in ("menu", "registry"):
                if entry.get(flag):
                    existing[flag] = True
            if entry.get("action") and not existing.get("action"):
                existing["action"] = entry["action"]

    groups = {}
    for entry in merged.values():
        entry["name"] = _pretty_name(entry)
        entry.pop("is_secondary", None)
        if _is_profile_noise(entry):
            entry["is_secondary"] = True
        groups.setdefault(_group_key(entry), []).append(entry)

    entries = []
    for key, members in groups.items():
        if len(members) > 1:
            primary = min(members, key=lambda e: _primary_score(e, key))
            for member in members:
                if member is not primary:
                    member["is_secondary"] = True
        entries.extend(members)

    # one row per display name (e.g. tray duplicate of the same app)
    unique = {}
    for entry in entries:
        key = entry["name"].lower()
        other = unique.get(key)
        if other is None:
            unique[key] = entry
            continue
        better = min(other, entry,
                     key=lambda e: (bool(e.get("is_secondary")),
                                    len(e.get("exe") or "")))
        unique[key] = better
    entries = list(unique.values())

    spotify = _spotify_entry()
    if spotify is not None:
        entries = [entry for entry in entries
                   if entry["name"].lower() != "spotify"]
        entries.append(spotify)

    entries.sort(key=lambda e: e["name"].lower())
    for entry in entries:
        entry.pop("menu", None)
        entry.pop("registry", None)
    return entries[:900]


def entry_visible(entry, show_unknown):
    """Default App Library hides hidden (system/helper), secondary (module) and
    non-.exe rows; the toggle reveals them."""
    if show_unknown:
        return True
    if entry.get("is_hidden") or entry.get("is_secondary"):
        return False
    return bool(entry.get("exe"))


def system_entries():
    media = [
        ("Play / Pause", "play_pause", "\u25b6"),
        ("Stop", "stop", "\u25a0"),
        ("Next track", "next", "\u23ed"),
        ("Previous track", "prev", "\u23ee"),
        ("Volume up", "vol_up", "+"),
        ("Volume down", "vol_down", "\u2212"),
        ("Mute", "mute", "\u2715"),
    ]
    entries = []
    for name, code, glyph in media:
        entries.append({"name": name, "kind": "system", "glyph": glyph,
                        "action": {"type": "media", "code": code}})
    entries.append({"name": "Hotkey\u2026", "kind": "system", "glyph": "\u2328",
                    "prompt": "hotkey"})
    entries.append({"name": "Type text\u2026", "kind": "system", "glyph": "Aa",
                    "prompt": "text"})
    entries.append({"name": "Open URL\u2026", "kind": "system", "glyph": "\u2197",
                    "prompt": "url"})
    return entries


class EntryDelegate(QStyledItemDelegate):
    """Full-width rounded card: icon, name, hover accent border and + hint."""

    ROW_HEIGHT = 52

    def sizeHint(self, option, index):  # noqa: N802 - Qt naming
        return QSize(0, self.ROW_HEIGHT)

    def paint(self, painter, option, index):  # noqa: N802 - Qt naming
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)

        rect = option.rect.adjusted(4, 2, -4, -2)
        hovered = bool(option.state & QStyle.State_MouseOver)
        selected = bool(option.state & QStyle.State_Selected)

        path = QPainterPath()
        path.addRoundedRect(rect, 10, 10)
        painter.fillPath(path, QColor("#1C1C26" if selected else "#18181F"))
        border = QColor(theme.ACCENT if (hovered or selected) else theme.BORDER)
        painter.strokePath(path, QPen(border, 1.5 if selected else 1.0))

        icon = index.data(Qt.DecorationRole)
        icon_size = 36
        # +2 px: icons carry no descenders, text ink sits slightly lower
        icon_rect = QRectF(rect.left() + 10,
                           rect.center().y() - icon_size / 2 + 2,
                           icon_size, icon_size)
        if isinstance(icon, QIcon) and not icon.isNull():
            pixmap = icon.pixmap(QSize(icon_size, icon_size))
            painter.drawPixmap(
                QPointF(icon_rect.center().x() - pixmap.width() / 2,
                        icon_rect.center().y() - pixmap.height() / 2),
                pixmap)
        else:
            payload = index.data(Qt.UserRole)
            glyph = (payload.get("glyph") if isinstance(payload, dict)
                     else None) or "\u25a6"
            glyph_font = QFont("Segoe UI Symbol")
            glyph_font.setStyleHint(QFont.SansSerif)
            glyph_font.setPixelSize(19)
            painter.setFont(glyph_font)
            painter.setPen(QColor(theme.ACCENT_SOFT))
            painter.drawText(icon_rect, Qt.AlignCenter, glyph)

        font = QFont(theme.SANS)
        font.setPixelSize(13)
        font.setWeight(QFont.Normal)
        painter.setFont(font)
        metrics = QFontMetrics(font)

        plus_width = 28
        text_rect = QRectF(icon_rect.right() + 10, rect.top(),
                           rect.width() - icon_rect.width() - 20 - plus_width,
                           rect.height())
        text = metrics.elidedText(index.data(Qt.DisplayRole) or "",
                                  Qt.ElideRight, int(text_rect.width()))
        painter.setPen(QColor(theme.TEXT))
        painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, text)

        if hovered:
            chip = QRectF(rect.right() - 28, rect.center().y() - 9, 18, 18)
            chip_path = QPainterPath()
            chip_path.addRoundedRect(chip, 6, 6)
            fill = QColor(theme.ACCENT)
            fill.setAlpha(42)
            painter.fillPath(chip_path, fill)
            stroke = QColor(theme.ACCENT)
            stroke.setAlpha(170)
            painter.strokePath(chip_path, QPen(stroke, 1.0))
            plus_font = QFont(theme.SANS)
            plus_font.setPixelSize(13)
            plus_font.setWeight(QFont.DemiBold)
            painter.setFont(plus_font)
            painter.setPen(QColor(theme.ACCENT_SOFT))
            painter.drawText(chip, Qt.AlignCenter, "+")

        painter.restore()


class EntryList(QListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("libraryList")
        self.setIconSize(QSize(36, 36))
        self.setSpacing(2)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragOnly)
        self.setUniformItemSizes(True)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.setItemDelegate(EntryDelegate(self))

    def mimeData(self, items):  # noqa: N802 - Qt naming
        data = QMimeData()
        if items:
            payload = items[0].data(Qt.UserRole)
            data.setData(MIME_ENTRY, json.dumps(payload).encode("utf-8"))
            data.setText(payload.get("name", ""))
        return data

    def startDrag(self, supported_actions):  # noqa: N802 - Qt naming
        item = self.currentItem()
        if item is None:
            return
        drag = QDrag(self)
        drag.setMimeData(self.mimeData([item]))
        drag.setPixmap(self._pill(item))
        drag.setHotSpot(QPoint(16, 18))
        drag.exec(Qt.CopyAction)

    def _pill(self, item):
        payload = item.data(Qt.UserRole) or {}
        pixmap = QPixmap(160, 36)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing, True)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0.75, 0.75, 158.5, 34.5), 8, 8)
        fill = QColor("#14141B")
        fill.setAlpha(245)
        painter.fillPath(path, fill)
        border = QColor(theme.ACCENT)
        border.setAlpha(210)
        painter.strokePath(path, QPen(border, 1.2))

        icon = item.icon()
        icon_size = 24
        x = 10.0
        if not icon.isNull():
            icon_pixmap = icon.pixmap(QSize(icon_size, icon_size))
            painter.drawPixmap(
                QPointF(x, (36 - icon_pixmap.height()) / 2), icon_pixmap)
        x += icon_size + 8

        font = QFont(theme.SANS)
        font.setPixelSize(12)
        painter.setFont(font)
        metrics = QFontMetrics(font)
        text = metrics.elidedText(payload.get("name", "") or "",
                                  Qt.ElideRight, int(160 - x - 10))
        painter.setPen(QColor(theme.TEXT))
        painter.drawText(QRectF(x, 0, 160 - x - 10, 36),
                         Qt.AlignVCenter | Qt.AlignLeft, text)
        painter.end()
        return pixmap


class AppLibraryPanel(QFrame):
    assign_requested = Signal(object)
    scan_finished = Signal()
    scan_ready = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self._apps = []
        self._system = system_entries()
        self._mode = 0
        self._query = ""
        self._show_unknown = False
        self._scanning = False
        self._rescan = False
        self.scan_ready.connect(self._on_scan_ready)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        header = QHBoxLayout()
        header.addWidget(QLabel(theme.caption("App library & akce")))
        header.addStretch(1)
        self.count_label = QLabel("")
        self.count_label.setStyleSheet("color: %s; font-size: 10px;" % theme.DIM)
        header.addWidget(self.count_label)
        layout.addLayout(header)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Hledat aplikaci\u2026")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._on_search)
        layout.addWidget(self.search)

        self.segment = SegmentedControl()
        self.segment.set_names(["Aplikace", "Syst\u00e9m"])
        self.segment.changed.connect(self._on_mode)
        layout.addWidget(self.segment, 0, Qt.AlignLeft)

        self.list = EntryList()
        self.list.itemDoubleClicked.connect(self._assign_item)
        layout.addWidget(self.list, 1)

        hint = QLabel("dvojklik přiřadí \u00b7 přetáhni na klávesu")
        hint.setStyleSheet("color: %s; font-size: 10px;" % theme.DIM)
        layout.addWidget(hint)
        self._hint = hint

        self.count_label.setText("načítám\u2026")
        QTimer.singleShot(80, self._scan)

    def resizeEvent(self, event):  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        self._hint.setVisible(self.height() >= 240)

    def set_show_unknown(self, value, rescan=True):
        if self._show_unknown == bool(value):
            return
        self._show_unknown = bool(value)
        self._populate()

    def _scan(self):
        self.count_label.setText("na\u010d\u00edt\u00e1m\u2026")
        if self._scanning:
            self._rescan = True
            return
        self._scanning = True
        threading.Thread(target=self._scan_worker,
                         args=(self._show_unknown,), daemon=True).start()

    def _scan_worker(self, show_unknown):
        try:
            apps = scan_installed_apps(show_unknown=show_unknown)
        except Exception:  # noqa: BLE001 - scanning must never break the UI
            apps = []
        self.scan_ready.emit(apps)

    def _on_scan_ready(self, apps):
        self._scanning = False
        self._apps = apps
        self._populate()
        self.scan_finished.emit()
        if self._rescan:
            self._rescan = False
            self._scan()

    def _on_mode(self, index):
        self._mode = index
        self._populate()

    def _on_search(self, text):
        self._query = (text or "").strip().lower()
        self._populate()

    def _populate(self):
        entries = self._apps if self._mode == 0 else self._system
        if self._mode == 0 and not self._show_unknown:
            entries = [e for e in entries if entry_visible(e, False)]
        self.list.clear()
        shown = 0
        for entry in entries:
            if self._query and self._query not in entry["name"].lower():
                continue
            icon = QIcon(entry.get("icon") or "")
            if icon.isNull() and entry.get("exe"):
                resolved = icon_for_exe(entry["exe"])
                if resolved is not None:
                    icon = resolved
            item = QListWidgetItem(icon, entry["name"])
            item.setData(Qt.UserRole, entry)
            if entry.get("running"):
                item.setToolTip("%s\n(running)" % entry["exe"])
            elif entry.get("exe"):
                item.setToolTip(entry["exe"])
            self.list.addItem(item)
            shown += 1
        total = len(entries)
        self.count_label.setText("%d / %d" % (shown, total) if self._query
                                 else "%d" % total)

    def _assign_item(self, item):
        entry = item.data(Qt.UserRole)
        if entry:
            self.assign_requested.emit(entry)
