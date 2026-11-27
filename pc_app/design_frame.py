"""Static design frame pushed to the device display (284x76 RGB565).

The frame contains the wallpaper plus every static UI element (header bar,
encoder ring tracks, telemetry labels, bar tracks, MODE caption) drawn exactly
like the firmware would draw them with the classic Adafruit GFX 5x7 font
(glyph table in gfx5x7.bin). Dynamic values - ring fills, telemetry numbers,
profile name, LIVE badge, key flash chip - are left empty; the firmware draws
them on top of this background.

Public API: build(path) -> raw big-endian RGB565 bytes (284*76*2).
"""
import os

from PIL import Image, ImageDraw

import theme
import wallpapers

PANEL_W, PANEL_H = 284, 76
FONT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "gfx5x7.bin")

# Colours mirrored from firmware renderChrome().
HEADER_TOP = (0x14, 0x0E, 0x22)
LABEL_COLOR = (0x9C, 0x8F, 0xB8)
TEXT_COLOR = (0xED, 0xE9, 0xFE)
TRACK_COLOR = (0x2A, 0x2A, 0x33)
ACCENT_SOFT = (0xC4, 0xB5, 0xFD)
BAR_TRACK = (0x23, 0x23, 0x2E)

RING_CENTERS = ((74, "E1"), (210, "E2"))
TELEMETRY = ((108, "CPU"), (142, "GPU"), (176, "RAM"))

_FONT = None


def _load_font():
    """Classic Adafruit GFX 5x7 font: 95 printable glyphs, 5 columns each."""
    global _FONT
    if _FONT is None:
        with open(FONT_PATH, "rb") as handle:
            _FONT = handle.read()
        if len(_FONT) < 95 * 5:
            raise OSError("gfx5x7.bin truncated")
    return _FONT


def _accent_rgb():
    value = str(theme.ACCENT or "#A855F7").lstrip("#")
    try:
        return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))
    except (ValueError, IndexError):
        return (0xA8, 0x55, 0xF7)


def _dim(color, num, den):
    """Firmware dim565() equivalent on 8-bit channels."""
    return tuple(c * num // den for c in color)


def _text(draw, x, y, text, color, size=1):
    """Draw text with the 5x7 GFX font (6 px advance per char, scaled)."""
    font = _load_font()
    for char in text:
        code = ord(char)
        if 32 <= code < 127:
            glyph = font[(code - 32) * 5:(code - 32) * 5 + 5]
            for col in range(5):
                bits = glyph[col]
                for row in range(7):
                    if bits >> row & 1:
                        if size == 1:
                            draw.point((x + col, y + row), fill=color)
                        else:
                            draw.rectangle(
                                [x + col * size, y + row * size,
                                 x + col * size + size - 1,
                                 y + row * size + size - 1], fill=color)
        x += 6 * size


def _default_background():
    """Firmware buildDefaultBackground() three-band diagonal gradient."""
    bands = ((0.275, (0x0B, 0x0B, 0x10)),
             (0.55, (0x13, 0x0E, 0x1E)),
             (1.01, (0x1E, 0x13, 0x33)))
    image = Image.new("RGB", (PANEL_W, PANEL_H))
    pixels = image.load()
    for y in range(PANEL_H):
        for x in range(PANEL_W):
            t = x / PANEL_W * 0.55 + y / PANEL_H * 0.45
            for limit, color in bands:
                if t < limit:
                    pixels[x, y] = color
                    break
    return image


def _chrome(draw, accent):
    """Static UI layer, coordinates and colours from firmware renderChrome()."""
    header_line = _dim(accent, 2, 5)

    # Header bar + accent underline + title.
    draw.rectangle([0, 0, PANEL_W - 1, 14], fill=HEADER_TOP)
    draw.rectangle([0, 15, PANEL_W - 1, 15], fill=header_line)
    _text(draw, 11, 4, "FINTR DECK", TEXT_COLOR)

    # Encoder ring tracks (dynamic arcs + values are drawn by the firmware).
    for cx, label in RING_CENTERS:
        for radius in (12, 13, 14):
            draw.ellipse([cx - radius, 41 - radius, cx + radius, 41 + radius],
                         outline=TRACK_COLOR)
        _text(draw, cx - 6, 16, label, ACCENT_SOFT)

    # Telemetry labels + bar tracks.
    for cx, label in TELEMETRY:
        _text(draw, cx - 9, 21, label, LABEL_COLOR)
        draw.rectangle([cx - 11, 44, cx + 10, 46], fill=BAR_TRACK)

    # MODE caption above the profile name field.
    _text(draw, PANEL_W // 2 - 12, 52, "MODE", ACCENT_SOFT)


def build(path):
    """Wallpaper (or default gradient) + static chrome -> RGB565 bytes."""
    try:
        if path:
            image = wallpapers.fit_image(Image.open(str(path)))
        else:
            image = _default_background()
    except (OSError, ValueError):
        image = _default_background()
    image = image.convert("RGB")

    draw = ImageDraw.Draw(image)
    _chrome(draw, _accent_rgb())
    return wallpapers.to_rgb565(image)
