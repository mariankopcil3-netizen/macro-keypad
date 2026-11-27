"""Build the release executable with PyInstaller.

Usage:
    python pc_app/build_release.py              # onedir build + zip
    python pc_app/build_release.py --onefile    # single exe instead

Output:
    dist/FintrDeck/          (onedir) or dist/FintrDeck.exe (onefile)
    dist/FintrDeck.zip       (onedir only)
"""
import argparse
import os
import subprocess
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DIST = ROOT / "dist"
NAME = "FintrDeck"

HOOKS = [
    "--windowed",
    "--name", NAME,
    "--icon", str(HERE / "assets" / "app_icon.ico"),
    "--noconfirm",
    "--clean",
    # assets must ship inside the bundle
    "--add-data", f"{HERE / 'assets'}{os.pathsep}assets",
    "--add-data", f"{HERE / 'gfx5x7.bin'}{os.pathsep}.",
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--onefile", action="store_true",
                        help="build a single self-extracting exe")
    args = parser.parse_args()

    cmd = [sys.executable, "-m", "PyInstaller"]
    if args.onefile:
        cmd.append("--onefile")
    else:
        cmd.append("--onedir")
    cmd += HOOKS + [str(HERE / "main.py")]

    print(" ".join(cmd))
    subprocess.check_call(cmd, cwd=str(ROOT))

    if args.onefile:
        print("built %s" % (DIST / (NAME + ".exe")))
        return

    exe_dir = DIST / NAME
    assert exe_dir.exists(), "build output missing"

    zip_path = DIST / (NAME + ".zip")
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in sorted(exe_dir.rglob("*")):
            if file.is_file():
                zf.write(file, file.relative_to(DIST))
    print("built %s (%.1f MB)" % (zip_path, zip_path.stat().st_size / 1e6))


if __name__ == "__main__":
    main()
