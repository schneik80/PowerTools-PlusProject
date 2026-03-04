"""
Generate updateTasks command icons.

Derived from the listTasks icons (clipboard + three blue lines):
  - keeps the grey clipboard body and the three blue report lines
  - adds a small blue pencil/edit mark in the top-right corner

Output: commands/updateTasks/resources/{16x16,32x32,64x64}{,-dark}.png

Run from the repo root:
  python3 commands/addtask/resources/btn_src/gen_updatetasks_icons.py
"""

import os
from PIL import Image, ImageDraw

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LISTTASKS_RES = os.path.join(SCRIPT_DIR, "..", "..", "..", "listTasks", "resources")
REPO_ROOT = os.path.join(SCRIPT_DIR, "..", "..", "..", "..")
OUT_DIR = os.path.join(REPO_ROOT, "commands", "updateTasks", "resources")

SIZES = [16, 32, 64]

THEMES = {
    "light": {"suffix": "", "blue": (25, 152, 244, 255)},
    "dark": {"suffix": "-dark", "blue": (25, 152, 244, 255)},
}


def draw_pencil(img: Image.Image, blue) -> Image.Image:
    """Draw a small diagonal pencil stroke in the top-right corner of *img*.

    The pencil is represented as a short anti-aliased line made from 2-3
    square pixel blocks at a 45-degree angle, consistent with the pixel-art
    style of the other icons at each size.
    """
    s = img.size[0]
    draw = ImageDraw.Draw(img)

    # Pencil tip region: top-right quadrant
    # Nib (small square at the tip)
    nib = max(1, round(s * 0.06))  # ~2 px @ 32
    shaft = max(1, round(s * 0.09))  # ~3 px @ 32

    # Positions — top-right area, above the report lines
    # Three blocks at 45° going from top-right toward centre
    offsets = [
        (round(s * 0.83), round(s * 0.06)),  # tip
        (round(s * 0.77), round(s * 0.12)),  # mid shaft
        (round(s * 0.71), round(s * 0.18)),  # base
    ]

    for x, y in offsets:
        draw.rectangle([x, y, x + shaft - 1, y + shaft - 1], fill=blue)

    # Eraser cap — 1 block perpendicular at the base
    ex = offsets[-1][0] - shaft
    ey = offsets[-1][1]
    draw.rectangle([ex, ey, ex + nib - 1, ey + nib - 1], fill=blue)

    return img


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    for theme_name, theme in THEMES.items():
        suffix = theme["suffix"]
        blue = theme["blue"]

        for size in SIZES:
            src_name = f"{size}x{size}{suffix}.png"
            src_path = os.path.join(LISTTASKS_RES, src_name)

            if not os.path.isfile(src_path):
                print(f"  SKIP — source not found: {src_path}")
                continue

            img = Image.open(src_path).convert("RGBA")
            img = draw_pencil(img, blue)

            dest = os.path.join(OUT_DIR, src_name)
            img.save(dest, "PNG")
            print(f"  wrote {dest}")

    print("\nDone.")


if __name__ == "__main__":
    main()
