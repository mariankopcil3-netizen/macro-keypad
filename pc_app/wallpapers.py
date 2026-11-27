"""ST7789 wallpaper gallery: built-in presets, custom uploads, RGB565 export.

Display is 284x76 landscape; conversion produces raw big-endian RGB565
(2 bytes/pixel, 43 168 bytes) ready for the `W:` serial transfer.
"""

import math
import random
import re
from pathlib import Path

from PIL import Image, ImageDraw
from PySide6.QtGui import QImage

from widgets import PANEL_H, PANEL_W

BUILTIN_DIR = Path(__file__).resolve().parent / "assets" / "wallpapers"
CUSTOM_DIR = BUILTIN_DIR / "custom"

BUILTIN_NAMES = ("aurora", "cybergrid", "neonwave", "dusk", "matrix", "ember")


def _make_aurora():
    img = Image.new("RGB", (PANEL_W, PANEL_H))
    px = img.load()
    for y in range(PANEL_H):
        for x in range(PANEL_W):
            t = x / (PANEL_W - 1)
            wave = math.sin(t * 5.4 + y / PANEL_H * 1.6) * 0.5 + 0.5
            px[x, y] = (
                int(10 + 46 * t * wave),
                int(8 + 26 * wave * (1 - t)),
                int(30 + 70 * (0.35 + 0.65 * wave)),
            )
    return img


def _make_cybergrid():
    img = Image.new("RGB", (PANEL_W, PANEL_H), (6, 8, 18))
    draw = ImageDraw.Draw(img)
    for x in range(0, PANEL_W, 14):
        draw.line([(x, 0), (x, PANEL_H)], fill=(15, 34, 58))
    for y in range(0, PANEL_H, 12):
        draw.line([(0, y), (PANEL_W, y)], fill=(13, 28, 48))
    for i in range(6):
        alpha = 90 - i * 14
        draw.line([(0, 56 + i), (PANEL_W, 56 + i)], fill=(6, alpha, alpha + 40))
    draw.line([(0, 55), (PANEL_W, 55)], fill=(34, 211, 238), width=2)
    return img


def _make_neonwave():
    img = Image.new("RGB", (PANEL_W, PANEL_H), (5, 4, 10))
    draw = ImageDraw.Draw(img)
    for phase, dim, bright in ((0.0, (60, 16, 80), (232, 62, 190)),
                               (1.4, (40, 20, 90), (124, 92, 255)),
                               (2.6, (70, 30, 50), (255, 120, 80))):
        for width, color in ((7, dim), (2, bright)):
            points = []
            for x in range(0, PANEL_W + 1, 3):
                y = PANEL_H / 2 + math.sin(x / 30.0 + phase) * 20
                points.append((x, y))
            draw.line(points, fill=color, width=width)
    return img


def _make_dusk():
    img = Image.new("RGB", (PANEL_W, PANEL_H))
    px = img.load()
    for y in range(PANEL_H):
        t = y / (PANEL_H - 1)
        if t < 0.62:
            k = t / 0.62
            color = (int(8 + 30 * k), int(6 + 12 * k), int(20 + 34 * k))
        else:
            k = (t - 0.62) / 0.38
            color = (int(38 + 92 * k), int(18 + 26 * k), int(54 - 26 * k))
        for x in range(PANEL_W):
            px[x, y] = color
    return img


def _make_matrix():
    rng = random.Random(7)
    img = Image.new("RGB", (PANEL_W, PANEL_H), (3, 8, 5))
    draw = ImageDraw.Draw(img)
    for x in range(0, PANEL_W, 7):
        head = rng.randint(-40, PANEL_H)
        for step in range(rng.randint(6, 16)):
            y0 = head - step * 9
            if y0 + 7 < 0 or y0 > PANEL_H:
                continue
            shade = 210 - step * 13
            draw.line([(x, y0), (x, y0 + 7)], fill=(2, max(shade, 40), 40), width=1)
    return img


def _make_ember():
    img = Image.new("RGB", (PANEL_W, PANEL_H))
    px = img.load()
    cx, cy = PANEL_W * 0.5, PANEL_H * 1.15
    max_d = math.hypot(PANEL_W, PANEL_H)
    for y in range(PANEL_H):
        for x in range(PANEL_W):
            d = math.hypot(x - cx, y - cy) / max_d
            glow = max(0.0, 1.0 - d * 1.7)
            px[x, y] = (
                int(6 + 150 * glow * glow),
                int(5 + 46 * glow * glow),
                int(8 + 20 * glow),
            )
    return img


_GENERATORS = {
    "aurora": _make_aurora,
    "cybergrid": _make_cybergrid,
    "neonwave": _make_neonwave,
    "dusk": _make_dusk,
    "matrix": _make_matrix,
    "ember": _make_ember,
}


def fit_image(image):
    """Center-crop to the display aspect ratio, then resize to 284x76."""
    image = image.convert("RGB")
    width, height = image.size
    target = PANEL_W / PANEL_H
    if width / height > target:
        new_w = int(height * target)
        left = (width - new_w) // 2
        image = image.crop((left, 0, left + new_w, height))
    else:
        new_h = int(width / target)
        top = (height - new_h) // 2
        image = image.crop((0, top, width, top + new_h))
    return image.resize((PANEL_W, PANEL_H), Image.LANCZOS)


def to_rgb565(image):
    """PIL RGB image (already fitted to 284x76) -> big-endian RGB565 bytes."""
    image = image.convert("RGB")
    data = image.tobytes()
    out = bytearray(len(data) // 3 * 2)
    j = 0
    for i in range(0, len(data), 3):
        r, g, b = data[i], data[i + 1], data[i + 2]
        value = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
        out[j] = value >> 8
        out[j + 1] = value & 0xFF
        j += 2
    return bytes(out)


def convert(path):
    """Write/refresh `<stem>.bin` next to the PNG, return (bin_path, raw)."""
    path = Path(path)
    image = fit_image(Image.open(path))
    raw = to_rgb565(image)
    bin_path = path.with_suffix(".bin")
    bin_path.write_bytes(raw)
    return bin_path, raw


def ensure_builtins():
    BUILTIN_DIR.mkdir(parents=True, exist_ok=True)
    CUSTOM_DIR.mkdir(parents=True, exist_ok=True)
    for name in BUILTIN_NAMES:
        png = BUILTIN_DIR / ("%s.png" % name)
        if png.exists():
            continue
        _GENERATORS[name]().save(png)
        convert(png)


def _display_name(stem):
    return re.sub(r"[_\-]+", " ", stem).strip().title()


def list_wallpapers():
    ensure_builtins()
    entries = []
    for folder, source in ((BUILTIN_DIR, "builtin"), (CUSTOM_DIR, "custom")):
        for png in sorted(folder.glob("*.png")):
            entries.append({
                "id": png.stem,
                "name": _display_name(png.stem),
                "path": png,
                "source": source,
            })
    return entries


def add_custom(source_path):
    """Copy an image into the custom gallery, cropped to the display."""
    ensure_builtins()
    slug = re.sub(r"[^a-z0-9]+", "_",
                  Path(source_path).stem.lower()).strip("_") or "wallpaper"
    target = CUSTOM_DIR / ("%s.png" % slug)
    counter = 1
    while target.exists():
        target = CUSTOM_DIR / ("%s_%d.png" % (slug, counter))
        counter += 1
    fit_image(Image.open(source_path)).save(target)
    convert(target)
    return target


def qimage(path):
    """PIL-fitted preview as QImage (detached copy)."""
    image = fit_image(Image.open(path))
    data = image.tobytes("raw", "RGB")
    return QImage(data, PANEL_W, PANEL_H, PANEL_W * 3,
                  QImage.Format_RGB888).copy()


def selected_raw(path):
    """RGB565 bytes for a wallpaper, using the cached .bin when present."""
    path = Path(path)
    bin_path = path.with_suffix(".bin")
    if not bin_path.exists() or bin_path.stat().st_mtime < path.stat().st_mtime:
        _, raw = convert(path)
        return raw
    return bin_path.read_bytes()
