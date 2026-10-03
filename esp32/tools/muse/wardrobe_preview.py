#!/usr/bin/env python3
"""Compile the real wardrobe renderer on the host and preview it without a board.

Pillow is needed only for preview files. The 412px preview approximates the
Watcher's existing low 256px idle canvas; it is not a hardware screenshot.
"""
import argparse
import ctypes
import os
from pathlib import Path
import subprocess
import tempfile

ESP32 = Path(__file__).resolve().parents[2]
OUTFITS = (
    "mild-knit", "warm-crochet", "cool-suede", "cold-layers", "wind-shell",
    "rain-shell", "heatwave-linen", "freezing-puffer", "cold-rain-parka",
    "fog-overshirt", "warm-rain-shell", "hot-wind-stripes", "hot-shorts",
)


class Pose(ctypes.Structure):
    _fields_ = [("mode", ctypes.c_int), ("t", ctypes.c_float), ("mode_t", ctypes.c_float),
                ("level", ctypes.c_float), ("happy", ctypes.c_float)]


def build_renderer(directory):
    library = Path(directory) / "wardrobe.so"
    subprocess.run([os.environ.get("CC", "cc"), "-std=c11", "-O2", "-Wall", "-Wextra",
                    "-Werror", "-shared", "-fPIC", str(ESP32 / "components/muse/muse_wardrobe.c"),
                    "-lm", "-o", str(library)], check=True)
    lib = ctypes.CDLL(str(library))
    for name in ("muse_wardrobe_valid", "muse_wardrobe_select"):
        method = getattr(lib, name)
        method.argtypes = [ctypes.c_char_p]
        method.restype = ctypes.c_bool
    lib.muse_wardrobe_current.argtypes = []
    lib.muse_wardrobe_current.restype = ctypes.c_char_p
    lib.muse_wardrobe_render.argtypes = [ctypes.POINTER(Pose), ctypes.c_bool]
    lib.muse_wardrobe_render.restype = ctypes.c_bool
    lib.muse_wardrobe_scale.argtypes = [ctypes.POINTER(ctypes.c_uint16)] + [ctypes.c_int] * 6
    lib.muse_wardrobe_scale.restype = ctypes.c_bool
    return lib


def render_pixels(lib, outfit, pose, size=256, ambient=True):
    if not lib.muse_wardrobe_select(outfit.encode()):
        raise ValueError("unknown outfit: " + outfit)
    if not lib.muse_wardrobe_render(ctypes.byref(pose), ambient):
        return None
    output = (ctypes.c_uint16 * (size * size))()
    if not lib.muse_wardrobe_scale(output, size, 0, size - 1, 0, size - 1, size):
        raise ValueError("invalid render size")
    return output


def pixel_image(pixels, size):
    from PIL import Image
    rgb = bytearray()
    for value in pixels:
        r, g, b = value >> 11, (value >> 5) & 63, value & 31
        rgb.extend(((r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)))
    return Image.frombytes("RGB", (size, size), bytes(rgb))


def watcher_image(pixels, size=256):
    from PIL import Image, ImageDraw
    screen = Image.new("RGB", (412, 412))
    # Same blank-row allowance as the existing low idle UI, 3 of 64 rows.
    top = 412 - 24 - size + 3 * (size // 64)
    screen.paste(pixel_image(pixels, size), ((412 - size) // 2, top))
    circle = Image.new("L", screen.size)
    ImageDraw.Draw(circle).ellipse((0, 0, 411, 411), fill=255)
    return Image.composite(screen, Image.new("RGB", screen.size, "#202020"), circle)


def main():
    from PIL import Image, ImageDraw
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--outfit", choices=OUTFITS, default="hot-shorts", help="outfit for animated GIF")
    parser.add_argument("--size", type=int, choices=(256, 320), default=256)
    parser.add_argument("--reaction", action="store_true", help="preview a single tap and return to idle")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        lib = build_renderer(temporary)
        sheet = Image.new("RGB", (4 * 412, 4 * 444), "#202020")
        draw = ImageDraw.Draw(sheet)
        for i, outfit in enumerate(OUTFITS):
            frame = watcher_image(render_pixels(lib, outfit, Pose(1, 0, 0, 0, 0), args.size), args.size)
            frame.save(args.output_dir / (outfit + ".png"))
            x, y = i % 4 * 412, i // 4 * 444
            sheet.paste(frame, (x, y + 24))
            draw.text((x + 8, y + 5), outfit + " (host preview)", fill="white")
        sheet.save(args.output_dir / "contact.png")
        frames = []
        for i in range(80):
            t = i / (20 if args.reaction else 10)
            remaining = 2.1 - t
            happy = min(1, remaining / .4) if args.reaction and .5 <= t < 2.1 else 0
            frame = render_pixels(lib, args.outfit, Pose(1, t, t, 0, happy), args.size)
            frames.append(watcher_image(frame, args.size))
        filename = args.outfit + ("-reaction" if args.reaction else "") + ".gif"
        frames[0].save(args.output_dir / filename, save_all=True,
                       append_images=frames[1:], duration=50 if args.reaction else 100,
                       loop=0, disposal=2)
    print("Host renderer previews (not device screenshots):", args.output_dir)


if __name__ == "__main__":
    main()
