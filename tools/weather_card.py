#!/usr/bin/env python3
"""Render local weather JSON as a Watcher JPEG; never fetch, upload, or send data."""

import argparse
import importlib.util
import json
import math
from pathlib import Path
import sys

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
SIZE = 412
MAX_INPUT_BYTES = 65536


def _module(name, relative_path):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_selector = _module("weather_outfit_selector", "skills/muse-weather-display/scripts/choose_outfit.py")
_assets = _module("weather_sprite_assets", "esp32/tools/muse/wardrobe_assets.py")


def _number(weather, key, lower, upper):
    value = weather.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{key} must be a finite JSON number or null")
    try:
        valid = math.isfinite(value) and lower <= value <= upper
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError(f"{key} must be finite and between {lower} and {upper}")
    return value


def _validate(weather):
    if not isinstance(weather, dict):
        raise ValueError("weather must be a JSON object")
    for key in ("temperature_c", "feels_like_c"):
        _number(weather, key, -150, 150)
    for key in ("temp_f", "feels_like_f", "high_f", "low_f"):
        _number(weather, key, -238, 302)
    for key in ("wind_kmh", "gust_kmh"):
        _number(weather, key, 0, 650)
    for key in ("wind_mph", "gust_mph"):
        _number(weather, key, 0, 400)
    _number(weather, "humidity_pct", 0, 100)
    conditions = weather.get("conditions")
    if isinstance(conditions, str):
        conditions = [conditions]
    if conditions is not None and (
        not isinstance(conditions, list) or len(conditions) > 64
        or any(not isinstance(tag, str) or len(tag) > 64 for tag in conditions)
    ):
        raise ValueError("conditions must contain at most 64 tags of at most 64 characters")
    if weather.get("high_f") is not None and weather.get("low_f") is not None:
        if weather["high_f"] < weather["low_f"]:
            raise ValueError("high_f cannot be below low_f")


def _fahrenheit(weather, metric, imperial):
    value = weather.get(metric)
    return value * 9 / 5 + 32 if value is not None else weather.get(imperial)


def display_lines(weather):
    """Return only supplied measurements, with metric inputs converted to F/mph."""
    _validate(weather)
    lines = []
    current = _fahrenheit(weather, "temperature_c", "temp_f")
    if current is not None:
        lines.append({"field": "current_temperature", "text": f"{current:.0f}°F"})
    else:
        feels = _fahrenheit(weather, "feels_like_c", "feels_like_f")
        if feels is not None:
            lines.append({"field": "feels_like", "text": f"Feels {feels:.0f}°F"})
    limits = []
    for key, label in (("high_f", "H"), ("low_f", "L")):
        value = weather.get(key)
        if value is not None:
            limits.append(f"{label} {value:.0f}°")
    if limits:
        lines.append({"field": "high_low", "text": "  ".join(limits)})
    wind = weather.get("wind_kmh")
    wind = wind / 1.609344 if wind is not None else weather.get("wind_mph")
    if wind is not None:
        lines.append({"field": "wind", "text": f"Wind {wind:.0f} mph"})
    humidity = weather.get("humidity_pct")
    if humidity is not None:
        lines.append({"field": "humidity", "text": f"Humidity {humidity:.0f}%"})
    return lines


def _pixel_text(text, scale, max_width):
    # Pillow's embedded bitmap font needs no platform font files. Older Pillow
    # versions expose this same bitmap through load_default().
    loader = getattr(ImageFont, "load_default_imagefont", ImageFont.load_default)
    font = loader()
    left, top, right, bottom = font.getbbox(text)
    mask = Image.new("1", (max(1, right - left), max(1, bottom - top)))
    ImageDraw.Draw(mask).text((-left, -top), text, font=font, fill=1)
    bounds = mask.getbbox()
    if bounds:
        mask = mask.crop(bounds)
    scale = max(1, min(scale, max_width // mask.width))
    return mask.resize((mask.width * scale, mask.height * scale), Image.Resampling.NEAREST)


def render_card(weather):
    """Return a 412px RGB image and delivery metadata; has no external effects."""
    lines = display_lines(weather)
    selection = _selector.choose_outfit(weather)
    image = Image.new("RGB", (SIZE, SIZE), "black")

    y = 38
    for line in lines:
        prominent = line["field"] in ("current_temperature", "feels_like")
        scale = 6 if prominent else 3 if line["field"] == "high_low" else 2
        mask = _pixel_text(line["text"], scale, 232 if prominent else 292)
        image.paste("white", ((SIZE - mask.width) // 2, y), mask)
        y += mask.height + (18 if prominent else 14)

    character, _ = _assets.character_crop(selection["asset_path"])
    factor = min(178 / character.width, 196 / character.height)
    character = character.resize(
        (max(1, round(character.width * factor)), max(1, round(character.height * factor))),
        Image.Resampling.NEAREST)
    image.paste(character, ((SIZE - character.width) // 2, 390 - character.height))
    return image, {**selection, "display_lines": lines, "units": "F/mph"}


def _read_weather(path):
    if path == "-":
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    else:
        with Path(path).open("rb") as stream:
            raw = stream.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise ValueError("weather JSON exceeds 64 KiB")
    def reject_constant(value):
        raise ValueError(f"weather JSON contains non-finite value: {value}")
    return json.loads(raw, parse_constant=reject_constant)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="local weather JSON file, or - for stdin")
    parser.add_argument("--output", type=Path, required=True, help="output JPEG filename")
    args = parser.parse_args(argv)
    try:
        image, metadata = render_card(_read_weather(args.input))
        image.save(args.output, format="JPEG", quality=95, subsampling=0,
                   progressive=False, optimize=True)
    except (OSError, ValueError, TypeError, KeyError, RecursionError) as error:
        print(f"weather_card: {error}", file=sys.stderr)
        return 2
    print(json.dumps({"output": str(args.output.resolve()), **metadata}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
