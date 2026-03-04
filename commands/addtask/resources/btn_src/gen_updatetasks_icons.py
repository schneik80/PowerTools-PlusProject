"""
Generate updateTasks command icons from commands/updateTasks/resources/src.svg.

Colors matched to existing addtask icon greys:
  light theme: #666666   (mid-grey, matches normal addtask icon body)
  dark theme:  #b9bec2   (light grey, matches dark addtask icon body)

Output: commands/updateTasks/resources/{16x16,32x32,64x64}{,-dark}.png

Run from the repo root:
  python3 commands/addtask/resources/btn_src/gen_updatetasks_icons.py

Requires: cairosvg  (pip install cairosvg)
"""

import os
import cairosvg

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.join(SCRIPT_DIR, "..", "..", "..", "..")
SRC_SVG = os.path.join(REPO_ROOT, "commands", "updateTasks", "resources", "src.svg")
OUT_DIR = os.path.join(REPO_ROOT, "commands", "updateTasks", "resources")

SIZES = [16, 32, 64]

THEMES = {
    "light": {"suffix": "", "color": "#666666"},
    "dark": {"suffix": "-dark", "color": "#b9bec2"},
}


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    with open(SRC_SVG, "r", encoding="utf-8") as f:
        src_svg = f.read()

    for theme_name, theme in THEMES.items():
        suffix = theme["suffix"]
        color = theme["color"]
        svg = src_svg.replace("#000000", color)

        for size in SIZES:
            dest = os.path.join(OUT_DIR, f"{size}x{size}{suffix}.png")
            cairosvg.svg2png(
                bytestring=svg.encode(),
                write_to=dest,
                output_width=size,
                output_height=size,
            )
            print(f"  wrote {dest}")

    print("\nDone.")


if __name__ == "__main__":
    main()
