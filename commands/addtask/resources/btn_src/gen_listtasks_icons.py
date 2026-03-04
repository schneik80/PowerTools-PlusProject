"""
Generate listTasks command icons.

Derived from the addtask icons:
  - keeps the grey clipboard body exactly
  - removes the blue "+" accent
  - draws three horizontal blue lines (list/report symbol) in the same corner

Output: commands/listTasks/resources/{16x16,32x32,64x64}{,-dark}.png

Run from the repo root:
  python3 commands/addtask/resources/btn_src/gen_listtasks_icons.py
"""

import os
from PIL import Image, ImageDraw

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ADDTASK_RES = os.path.join(SCRIPT_DIR, "..")  # addtask/resources/
REPO_ROOT = os.path.join(SCRIPT_DIR, "..", "..", "..", "..")  # project root
OUT_DIR = os.path.join(REPO_ROOT, "commands", "listTasks", "resources")

SIZES = [16, 32, 64]

THEMES = {
    "light": {"suffix": "", "blue": (25, 152, 244, 255)},  # #1998f4
    "dark": {
        "suffix": "-dark",
        "blue": (25, 152, 244, 255),
    },  # same blue, grey body differs
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_blue(r, g, b, a, threshold=60) -> bool:
    """Return True if the pixel is predominantly blue (the accent colour)."""
    return a > 20 and b > 120 and b > r + threshold and b > g + 20


def strip_blue(img: Image.Image) -> Image.Image:
    """Return a copy of *img* with all blue accent pixels made transparent."""
    out = img.copy()
    pixels = out.load()
    for y in range(img.height):
        for x in range(img.width):
            r, g, b, a = pixels[x, y]
            if _is_blue(r, g, b, a):
                pixels[x, y] = (0, 0, 0, 0)
    return out


def draw_report_lines(img: Image.Image, blue) -> Image.Image:
    """Draw three horizontal lines in the bottom-right of *img* to suggest a report/list.

    Geometry is tuned so lines are clearly separated at every target size:
      - line height  = max(1, s // 16)   → 1 px @16, 2 px @32, 4 px @64
      - gap between  = max(1, s // 16)   → equal to line height
      - block starts at 60 % of height, ends ~92 %
      - lines start at 60 % of width,   end ~93 %
    """
    s = img.size[0]

    x0 = round(s * 0.60)
    x1 = round(s * 0.93)

    lh = max(1, s // 16)  # line height
    gap = max(1, s // 16)  # gap between lines (equal to line height → clean separation)

    # Stack 3 lines from the top of the bottom region
    y_start = round(s * 0.60)
    tops = [y_start + i * (lh + gap) for i in range(3)]

    draw = ImageDraw.Draw(img)
    for top in tops:
        draw.rectangle([x0, top, x1, top + lh - 1], fill=blue)

    return img


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    for theme_name, theme in THEMES.items():
        suffix = theme["suffix"]
        blue = theme["blue"]

        for size in SIZES:
            src_name = f"{size}x{size}{suffix}.png"
            src_path = os.path.join(ADDTASK_RES, src_name)

            if not os.path.isfile(src_path):
                print(f"  SKIP — source not found: {src_path}")
                continue

            img = Image.open(src_path).convert("RGBA")

            # 1. Remove the blue "+" from the addtask base
            img = strip_blue(img)

            # 2. Draw the three horizontal blue lines (report symbol)
            img = draw_report_lines(img, blue)

            dest = os.path.join(OUT_DIR, src_name)
            img.save(dest, "PNG")
            print(f"  wrote {dest}")

    print("\nDone.")


if __name__ == "__main__":
    main()
