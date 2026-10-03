#!/usr/bin/env python3
"""Choose a Muse outfit from weather JSON on stdin or in one supplied file."""

import argparse
from decimal import Decimal
import json
import math
from pathlib import Path
import sys


ASSETS_DIR = (Path(__file__).resolve().parent / "../../../assets/weather").resolve()
NUMERIC_FIELDS = (
    "feels_like_c", "feels_like_f", "temperature_c", "temp_f",
    "wind_kmh", "gust_kmh", "wind_mph", "gust_mph",
)
ALIASES = {"fair": "clear", "rainfall": "rain", "thunderstorms": "thunderstorm"}


def _number(weather, key):
    value = weather.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("%s must be a finite JSON number or null" % key)
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError("%s must be finite" % key)
    return value


def _weather_inputs(weather):
    if not isinstance(weather, dict):
        raise ValueError("weather must be a JSON object")
    numbers = {key: _number(weather, key) for key in NUMERIC_FIELDS}
    temperature, basis = None, "unavailable"
    for key, candidate_basis in (
        ("feels_like_c", "feels_like"), ("feels_like_f", "feels_like"),
        ("temperature_c", "current"), ("temp_f", "current"),
    ):
        if numbers[key] is not None:
            temperature = numbers[key]
            if key.endswith("_f"):
                # Decimal conversion keeps exact boundaries such as 71.6F -> 22C.
                temperature = float((Decimal(str(temperature)) - 32) * 5 / 9)
            basis = candidate_basis
            break

    conditions = weather.get("conditions")
    if conditions is None:
        conditions = []
    if isinstance(conditions, str):
        conditions = [conditions]
    if not isinstance(conditions, list) or any(not isinstance(c, str) for c in conditions):
        raise ValueError("conditions must be an array of strings or a string")
    tags = set()
    for condition in conditions:
        tag = condition.strip().lower().replace("-", "_").replace(" ", "_")
        tags.add(ALIASES.get(tag, tag))

    winds = []
    for metric, imperial in (("wind_kmh", "wind_mph"), ("gust_kmh", "gust_mph")):
        value = numbers[metric]
        if value is None and numbers[imperial] is not None:
            value = numbers[imperial] * 1.609344
            if not math.isfinite(value):
                raise ValueError("%s conversion must be finite" % imperial)
        if value is not None and value < 0:
            raise ValueError("wind and gust speeds cannot be negative")
        winds.append(value)
    return temperature, basis, tags, winds


def _in_range(temperature, bounds):
    if temperature is None:
        return False
    lower, upper = bounds.get("minInclusiveC"), bounds.get("maxExclusiveC")
    return (lower is None or temperature >= lower) and (upper is None or temperature < upper)


def choose_outfit(weather, assets_dir=ASSETS_DIR):
    """Return selection metadata; only read the catalog and check its asset exists."""
    assets_dir = Path(assets_dir).resolve()
    with (assets_dir / "weather-rules.json").open(encoding="utf-8") as stream:
        rules = json.load(stream)
    with (assets_dir / "catalog.json").open(encoding="utf-8") as stream:
        outfits = json.load(stream)
    if isinstance(outfits, dict):
        outfits = outfits["outfits"]

    temperature, basis, tags, winds = _weather_inputs(weather)
    thresholds = rules["windyThreshold"]
    windy = None if all(value is None for value in winds) else (
        (winds[0] is not None and winds[0] >= thresholds["windMinKmh"])
        or (winds[1] is not None and winds[1] >= thresholds["gustMinKmh"])
    )
    outfit_id, rule_order = rules["fallbackOutfitId"], None
    for rule in rules["selectionRules"]:
        matches = []
        for field, expected in rule["if"].items():
            if field == "conditionGroup":
                matches.append(bool(tags.intersection(rules["conditionGroups"][expected])))
            elif field == "temperatureBand":
                matches.append(_in_range(temperature, rules["temperatureBands"][expected]))
            elif field == "temperatureRangeC":
                matches.append(_in_range(temperature, expected))
            elif field == "windy":
                matches.append(windy is not None and windy == expected)
            else:
                raise ValueError("unsupported rule condition: %s" % field)
        if all(matches):
            outfit_id, rule_order = rule["outfitId"], rule["order"]
            break

    selected = next((outfit for outfit in outfits if outfit["id"] == outfit_id), None)
    if selected is None:
        raise ValueError("outfit missing from catalog: %s" % outfit_id)
    asset_path = (assets_dir / selected["file"]).resolve()
    if not asset_path.is_file():
        raise FileNotFoundError("outfit asset does not exist: %s" % asset_path)
    return {
        "outfit_id": outfit_id,
        "asset_path": str(asset_path),
        "temperature_basis": basis,
        "rule_order": rule_order,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", nargs="?", default="-", help="JSON filename, or - for stdin (default)")
    parser.add_argument("--assets-dir", type=Path, default=ASSETS_DIR,
                        help="directory containing catalog.json, weather-rules.json and the PNGs")
    args = parser.parse_args(argv)
    try:
        if args.input == "-":
            weather = json.load(sys.stdin)
        else:
            with Path(args.input).open(encoding="utf-8") as stream:
                weather = json.load(stream)
        result = choose_outfit(weather, assets_dir=args.assets_dir)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print("choose_outfit: %s" % error, file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
