"""Regenerate the HookyMod app icons from the user-supplied master PNG.

Install Pillow, then run ``python tools/update_brand_assets.py`` from any
working directory. The 512x512 master itself is copied unchanged to the web
and desktop splash screens; only the platform icon variants are resized.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
MASTER = ROOT / "assets" / "HookyMod-logo.png"
ICONS = ROOT / "src-tauri" / "icons"


def main() -> None:
    with Image.open(MASTER) as source:
        if source.format != "PNG" or source.size != (512, 512):
            raise ValueError("The HookyMod master must be a 512x512 PNG.")
        image = source.convert("RGBA")

    for destination in (
        ROOT / "hookycreator" / "web" / "HookyMod.png",
        ROOT / "src-tauri" / "desktop" / "HookyMod.png",
    ):
        shutil.copyfile(MASTER, destination)

    for filename, size in (
        ("32x32.png", 32),
        ("128x128.png", 128),
        ("128x128@2x.png", 256),
    ):
        image.resize((size, size), Image.Resampling.LANCZOS).save(ICONS / filename)

    # Supply the exact same resampling for every size on both platforms.
    sizes = (16, 24, 32, 48, 64, 128, 256, 512, 1024)
    frames = {
        size: image if size == 512 else image.resize((size, size), Image.Resampling.LANCZOS)
        for size in sizes
    }
    image.save(
        ICONS / "icon.ico",
        sizes=[(size, size) for size in sizes if size <= 256],
        append_images=[frames[size] for size in sizes if size <= 256],
    )
    image.save(
        ICONS / "icon.icns",
        append_images=[frames[size] for size in (32, 64, 128, 256, 512, 1024)],
    )
    print("Updated web, desktop, Windows and macOS icons from", MASTER)


if __name__ == "__main__":
    main()
