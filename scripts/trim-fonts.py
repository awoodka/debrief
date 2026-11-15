"""Cut the two Newsreader files in templates/fonts/ down to what site.css uses.

The upright face keeps its weight axis from 400 to 600 and its optical size from 14 to 32 (the type
sizes in use). The italic face only sets quotes in running text, so it becomes one static instance at
weight 400 and optical size 19. Start from the full Latin variable files from Google Fonts (SIL OFL,
templates/fonts/OFL.txt) and run, in any Python with fonttools and brotli installed:

    python scripts/trim-fonts.py <newsreader-full.woff2> <newsreader-italic-full.woff2>
"""
import sys
from pathlib import Path

from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

OUT = Path(__file__).resolve().parent.parent / "templates" / "fonts"
CUTS = (("newsreader.woff2", {"wght": (400, 600), "opsz": (14, 32)}),
        ("newsreader-italic.woff2", {"wght": 400, "opsz": 19}))


def main(sources):
    for src, (name, limits) in zip(sources, CUTS):
        font = instancer.instantiateVariableFont(TTFont(src), limits)
        font.flavor = "woff2"
        font.save(OUT / name)
        print(f"{name}: {(OUT / name).stat().st_size:,} bytes")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1:])
