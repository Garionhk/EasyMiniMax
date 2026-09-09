"""Build the EasyMiniMax icon pack from one master PNG.

    python tools/make_icons.py

Reads EasyMiniMax.png and writes assets/icons/ - a multi-resolution .ico for
Windows, plus PNGs for anything that wants one.

Two things this does that a plain resize does not:

* **Trims the transparent margin.** The master has ~10% padding on every side.
  Windows adds its own spacing around an icon, so shipping that padding just
  makes the artwork smaller in the taskbar for no reason.
* **Sharpens the small sizes.** Below 32 pixels a smooth downscale goes soft
  and the star's face turns to mush; a touch of unsharp mask keeps the
  silhouette crisp where it matters most.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
MASTER = ROOT / "EasyMiniMax.png"
OUT = ROOT / "assets" / "icons"

#: Everything Windows asks for. 16/20/24/32 are the taskbar and title bar,
#: 40/48/64 are Explorer views, 128/256 are the large tiles and the Store.
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)

#: Sizes small enough that a plain downscale loses the shape.
SHARPEN_BELOW = 48


def trimmed(image: Image.Image) -> Image.Image:
    """Crop the transparent border, keeping the result square."""
    alpha = image.split()[3]
    box = alpha.getbbox()
    if not box:
        return image

    left, top, right, bottom = box
    width, height = right - left, bottom - top
    side = max(width, height)

    # Re-centre on a square canvas so a non-square mark is not stretched.
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.paste(image.crop(box), ((side - width) // 2, (side - height) // 2))
    return square


def resized(image: Image.Image, size: int) -> Image.Image:
    out = image.resize((size, size), Image.LANCZOS)
    if size < SHARPEN_BELOW:
        out = out.filter(ImageFilter.UnsharpMask(radius=0.6, percent=90, threshold=0))
    return out


def main() -> int:
    if not MASTER.is_file():
        print(f"No master icon at {MASTER}")
        return 1

    master = trimmed(Image.open(MASTER).convert("RGBA"))
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"master {MASTER.name}: trimmed to {master.size[0]}px")

    frames = [resized(master, size) for size in ICO_SIZES]

    ico = OUT / "EasyMiniMax.ico"
    frames[-1].save(ico, format="ICO",
                    sizes=[(s, s) for s in ICO_SIZES],
                    append_images=frames[:-1])
    print(f"  {ico.relative_to(ROOT)}  ({ico.stat().st_size / 1024:.0f} KB, "
          f"{len(ICO_SIZES)} sizes)")

    for size, frame in zip(ICO_SIZES, frames):
        if size in (16, 32, 48, 256):
            path = OUT / f"EasyMiniMax-{size}.png"
            frame.save(path)
            print(f"  {path.relative_to(ROOT)}")

    full = OUT / "EasyMiniMax-1024.png"
    master.save(full)
    print(f"  {full.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
