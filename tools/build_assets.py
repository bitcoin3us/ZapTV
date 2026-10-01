#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 ZapTV.org
#
# This file is part of ZapTV. ZapTV is free software: you can redistribute
# it and/or modify it under the terms of the GNU General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. It is distributed WITHOUT
# ANY WARRANTY; see the GNU General Public License (LICENSE) for details.

"""Regenerate ZapTV's logo PNGs from the SVG sources in artwork/.

    python3 tools/build_assets.py [--check]

Sources, from the ZapTV logo family (copied in with their C2PA <metadata>
block removed and nothing else changed). Only files inside this repo are
read:

    artwork/zaptv-logo.svg       the TV mark, viewBox 0 0 634 567
    artwork/zaptv-wordmark.svg   the ZAPTV wordmark: #111 ink, cream halo

Outputs, under org.zaptv.app/:

    icon_64x64.png                  launcher icon (RGBA)
    res/mipmap-mdpi/icon_64x64.png  legacy copy of the icon (indexed)
    res/logo_tv.png                 welcome screen: the TV mark alone (RGBA)
    res/zaptv_lockup.png            launch splash and About logo (RGBA)

The icon is the whole logo viewBox fitted to the tile width, 64x57 at y=3
in a transparent 64x64 tile, the convention shared by the ZapTV family.
The splash is the horizontal lockup: the TV mark on the left at 2.2x the
wordmark's height, a gap of 0.35x that height, then the wordmark centred
on the mark. One image serves both themes because the cream halo keeps the
dark ink readable on a dark background. When an output changes size, give
it a new file name (and update zaptv.py): LVGL caches image headers by path
until reboot, so a same-named file replaced in place is drawn at the old
size with its rows wrapped. zaptv_lockup.png replaced splash_light.png and
splash_dark.png for that reason.

Each PNG keeps its colour mode. The legacy icon stays an indexed palette
with transparency, because the lodepng in older MicroPythonOS builds
silently fails to draw RGBA truecolour PNGs; the rest stay RGBA.

Renders with rsvg-convert (brew install librsvg), well above the target
size, then downscales once with LANCZOS for clean edges. rsvg-convert
ignores the logo's CSS animation, so the bolt renders in its resting state.

--check writes nothing: it lists what would change and exits with status 1
if anything would.
"""

import math
import os
import re
import subprocess
import sys
import tempfile

from PIL import Image, ImageChops

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
ART = os.path.join(ROOT, "artwork")
APP = os.path.join(ROOT, "org.zaptv.app")

LOGO_SVG = os.path.join(ART, "zaptv-logo.svg")
WORDMARK_SVG = os.path.join(ART, "zaptv-wordmark.svg")

# Launcher icon: the logo fitted to the full tile width (64x57), centred
# vertically at y=3 in a transparent 64x64 tile.
ICON_PX = 64
ICON_MARK = (64, 57)
# Welcome screen: the TV mark alone, 96 px wide.
LOGO_TV = (96, 86)
# Horizontal lockup proportions, in units of the wordmark's viewBox height.
LOCKUP_MARK_H = 2.2
LOCKUP_GAP = 0.35
# Splash and About logo width. 256 px keeps the on-device decode small;
# scaling a big source on the device would waste several MB of RAM.
SPLASH_W = 256
# Render size multiplier over the final pixel size before the LANCZOS
# downscale: about 1200 px wide for the icon, 2048 px for the splash.
SUPERSAMPLE_ICON = 2       # x the 634 unit viewBox width = 1268 px
SUPERSAMPLE_SPLASH = 8     # x 256 px = 2048 px


def read_svg(path):
    """Return (viewBox floats, inner markup) of an SVG file."""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    # Defensive: the copies in artwork/ already have it removed.
    text = re.sub(r"<metadata>.*?</metadata>", "", text, flags=re.S)
    vb = [float(v) for v in re.search(r'viewBox="([^"]+)"', text).group(1).split()]
    start = text.index(">", text.index("<svg")) + 1
    return vb, text[start:text.rindex("</svg>")]


def fmt(v):
    return ("%.4f" % v).rstrip("0").rstrip(".")


def lockup_svg(pad_to_aspect):
    """The horizontal lockup as one SVG document, with its viewBox padded
    top and bottom so it has exactly the requested width/height ratio. The
    padding lets the final image take a whole number of pixels in height
    without stretching the artwork."""
    lvb, lbody = read_svg(LOGO_SVG)
    wvb, wbody = read_svg(WORDMARK_SVG)
    ww, wh = wvb[2], wvb[3]
    mh = LOCKUP_MARK_H * wh
    mw = mh * lvb[2] / lvb[3]
    gap = LOCKUP_GAP * wh
    width, height = mw + gap + ww, mh
    pad = (width / pad_to_aspect - height) / 2
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 %s %s %s">'
        % (fmt(-pad), fmt(width), fmt(height + 2 * pad))
        + '<svg x="0" y="0" width="%s" height="%s" viewBox="%s">%s</svg>'
        % (fmt(mw), fmt(mh), " ".join(fmt(v) for v in lvb), lbody)
        + '<svg x="%s" y="%s" width="%s" height="%s" viewBox="%s">%s</svg>'
        % (fmt(mw + gap), fmt((mh - wh) / 2), fmt(ww), fmt(wh),
           " ".join(fmt(v) for v in wvb), wbody)
        + "</svg>"
    )


def lockup_aspect():
    lvb, _ = read_svg(LOGO_SVG)
    wvb, _ = read_svg(WORDMARK_SVG)
    mh = LOCKUP_MARK_H * wvb[3]
    return (mh * lvb[2] / lvb[3] + LOCKUP_GAP * wvb[3] + wvb[2]) / mh


def render(svg_path, width, height):
    """SVG file -> RGBA image of exactly width x height."""
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "out.png")
        subprocess.run(["rsvg-convert", "-w", str(width), "-h", str(height),
                        "-o", out, svg_path], check=True, capture_output=True)
        img = Image.open(out).convert("RGBA")
        img.load()
    if img.size != (width, height):
        sys.exit("rsvg-convert gave %s, wanted %s" % (img.size, (width, height)))
    return img


def render_text(svg_text, width, height):
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "in.svg")
        with open(path, "w", encoding="utf-8") as f:
            f.write(svg_text)
        return render(path, width, height)


def logo_big():
    vb, _ = read_svg(LOGO_SVG)
    return render(LOGO_SVG, round(vb[2] * SUPERSAMPLE_ICON), round(vb[3] * SUPERSAMPLE_ICON))


def to_indexed(img):
    # method=2 (FASTOCTREE) is the one that carries alpha into the palette.
    return img.quantize(colors=255, method=Image.Quantize.FASTOCTREE,
                        dither=Image.Dither.FLOYDSTEINBERG)


def build_icon(big):
    mark = big.resize(ICON_MARK, Image.LANCZOS)
    tile = Image.new("RGBA", (ICON_PX, ICON_PX), (0, 0, 0, 0))
    tile.paste(mark, ((ICON_PX - ICON_MARK[0]) // 2, (ICON_PX - ICON_MARK[1]) // 2))
    return tile


def build_logo_tv(big):
    return big.resize(LOGO_TV, Image.LANCZOS)


def build_splash():
    # Round the height up, as rsvg-convert does for a width-only render
    # (256 px wide gives 85 rows); lockup_svg pads the spare fraction of a
    # row evenly above and below the artwork.
    height = math.ceil(SPLASH_W / lockup_aspect() - 1e-9)
    svg = lockup_svg(SPLASH_W / height)
    big = render_text(svg, SPLASH_W * SUPERSAMPLE_SPLASH, height * SUPERSAMPLE_SPLASH)
    return big.resize((SPLASH_W, height), Image.LANCZOS)


def same(path, img):
    if not os.path.exists(path):
        return False
    old = Image.open(path)
    if old.mode != img.mode or old.size != img.size:
        return False
    return ImageChops.difference(old.convert("RGBA"), img.convert("RGBA")).getbbox(alpha_only=False) is None


def write(img, relpath, check):
    path = os.path.join(APP, relpath)
    if same(path, img):
        print("  unchanged   %s" % relpath)
        return False
    if check:
        print("  WOULD WRITE %s %s %s" % (relpath, img.mode, img.size))
        return True
    img.save(path, optimize=True)
    print("  wrote       %s %s %s (%d bytes)"
          % (relpath, img.mode, img.size, os.stat(path).st_size))
    return True


def main():
    check = "--check" in sys.argv[1:]
    for path in (LOGO_SVG, WORDMARK_SVG):
        if not os.path.exists(path):
            sys.exit("missing source: %s" % os.path.relpath(path, ROOT))
    try:
        subprocess.run(["rsvg-convert", "--version"], check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError):
        sys.exit("rsvg-convert not found; install it with: brew install librsvg")

    print("building from %s" % os.path.relpath(ART, os.getcwd()))
    big = logo_big()
    icon = build_icon(big)
    splash = build_splash()
    changed = False
    changed |= write(icon, "icon_64x64.png", check)
    changed |= write(to_indexed(icon), "res/mipmap-mdpi/icon_64x64.png", check)
    changed |= write(build_logo_tv(big), "res/logo_tv.png", check)
    changed |= write(splash, "res/zaptv_lockup.png", check)
    if check:
        print("changes pending" if changed else "everything already current")
        sys.exit(1 if changed else 0)
    print("updated" if changed else "everything already current")


if __name__ == "__main__":
    main()
