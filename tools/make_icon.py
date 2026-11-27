"""Generate pc_app/assets/app_icon.ico from the icon source image.

Usage:  python tools/make_icon.py  (source: app_icon_source.jpg)
"""

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "pc_app" / "assets" / "app_icon_source.jpg"
OUT = ROOT / "pc_app" / "assets" / "app_icon.ico"
SIZES = [16, 32, 48, 64, 128, 256]


def main():
    img = Image.open(SRC).convert("RGBA")
    width, height = img.size
    side = min(width, height)
    left = (width - side) // 2
    top = (height - side) // 2
    img = img.crop((left, top, left + side, top + side))

    base = img.resize((256, 256), Image.LANCZOS)
    frames = [base] + [base.resize((s, s), Image.LANCZOS)
                       for s in reversed(SIZES[:-1])]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    base.save(OUT, format="ICO", append_images=frames[1:],
              sizes=[(s, s) for s in SIZES])
    print("saved %s (%s)" % (OUT, ", ".join(str(s) for s in SIZES)))


if __name__ == "__main__":
    main()
