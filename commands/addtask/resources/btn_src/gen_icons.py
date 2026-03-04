"""
Generate button-row icons for the three date-shortcut buttons.

Color palette matched to the existing PowerTools add-in icons:
  light  (16x16.png / 32x32.png)      — badge fill #666666, text #ffffff
  dark   (16x16-dark.png / 32x32-dark.png) — badge fill #b7bcc1, text #1a1a1a

Run from the repo root or this folder:
  python3 commands/addtask/resources/btn_src/gen_icons.py
"""

import os
from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BUTTONS = [
    ("btn_tomorrow", "+1D"),
    ("btn_end_of_week", "EOW"),
    ("btn_in_1_week", "+1W"),
]

SIZES = [16, 32]

THEMES = {
    "light": {
        "bg": (102, 102, 102, 255),  # #666666
        "fg": (255, 255, 255, 255),  # #ffffff
        "suffix": "",
    },
    "dark": {
        "bg": (183, 188, 193, 255),  # #b7bcc1
        "fg": (26, 26, 26, 255),  # #1a1a1a
        "suffix": "-dark",
    },
}

OUT_ROOT = os.path.join(os.path.dirname(__file__), "..")  # resources/

# ---------------------------------------------------------------------------
# Font helpers
# ---------------------------------------------------------------------------


def _load_font(size_px: int):
    """Load Helvetica Bold (matches SVG font-family="Helvetica-Bold").
    Falls back to Arial Bold, then Menlo, then the Pillow default."""
    candidates = [
        ("/System/Library/Fonts/Helvetica.ttc", 1),  # Helvetica Bold (macOS)
        ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 0),
        ("/Library/Fonts/Arial Bold.ttf", 0),
        ("/System/Library/Fonts/Menlo.ttc", 0),
    ]
    for path, index in candidates:
        if os.path.isfile(path):
            try:
                return ImageFont.truetype(path, size_px, index=index)
            except Exception:
                pass
    return ImageFont.load_default()


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------


def render(label: str, size: int, bg, fg) -> Image.Image:
    """Draw a rounded-rectangle badge with centred label text."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    radius = max(2, size // 8)
    draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=bg)

    # Font size: SVGs specify font-size=13 in a 32x32 canvas → ratio 13/32.
    # Use round() to stay as close as possible at each target size.
    # floor-clamp to 7px minimum so the 16x16 icon remains legible.
    font_px = max(7, round(size * 13 / 32))
    font = _load_font(font_px)

    # Centre the text
    bbox = draw.textbbox((0, 0), label, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    x = (size - tw) / 2 - bbox[0]
    y = (size - th) / 2 - bbox[1]
    draw.text((x, y), label, font=font, fill=fg)

    return img


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    for btn_id, label in BUTTONS:
        out_dir = os.path.join(OUT_ROOT, btn_id)
        os.makedirs(out_dir, exist_ok=True)

        for theme_name, theme in THEMES.items():
            for size in SIZES:
                img = render(label, size, theme["bg"], theme["fg"])
                filename = f"{size}x{size}{theme['suffix']}.png"
                dest = os.path.join(out_dir, filename)
                img.save(dest, "PNG")
                print(f"  wrote {dest}")

    print("\nDone.")


if __name__ == "__main__":
    main()
