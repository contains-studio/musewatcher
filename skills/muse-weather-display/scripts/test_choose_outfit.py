"""Scenario checks for the portable weather outfit selector; no device access."""

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from choose_outfit import NUMERIC_FIELDS, ASSETS_DIR, choose_outfit


class ChooseOutfitTests(unittest.TestCase):
    def assert_outfit(self, expected, **weather):
        result = choose_outfit(weather)
        self.assertEqual(result["outfit_id"], expected)
        self.assertTrue(Path(result["asset_path"]).is_absolute())
        self.assertTrue(Path(result["asset_path"]).is_file())
        return result

    def test_temperature_boundaries(self):
        for temperature, expected in (
            (-0.001, "freezing-puffer"), (0, "cold-layers"), (6.999, "cold-layers"),
            (7, "cool-suede"), (13.999, "cool-suede"), (14, "mild-knit"),
            (21.999, "mild-knit"), (22, "warm-crochet"), (23.999, "warm-crochet"),
            (24, "hot-shorts"), (31.999, "hot-shorts"), (32, "hot-shorts"),
        ):
            with self.subTest(temperature=temperature):
                self.assert_outfit(expected, conditions=["clear"], feels_like_c=temperature)

    def test_72f_fair_current_temperature_fallback(self):
        result = self.assert_outfit("warm-crochet", conditions=["Fair"], temp_f=72, wind_mph=3)
        self.assertEqual(result["temperature_basis"], "current")
        self.assertEqual(result["rule_order"], 16)
        self.assertEqual(Path(result["asset_path"]).name, "warm-crochet.png")

    def test_exact_fahrenheit_boundaries(self):
        for temperature, expected in ((32, "cold-layers"), (44.6, "cool-suede"),
                                      (57.2, "mild-knit"), (71.6, "warm-crochet"),
                                      (89.6, "heatwave-linen")):
            with self.subTest(temperature=temperature):
                self.assert_outfit(expected, temp_f=temperature)

    def test_feels_like_precedes_current_and_celsius_precedes_fahrenheit(self):
        result = self.assert_outfit("cool-suede", feels_like_f=50, temperature_c=30)
        self.assertEqual(result["temperature_basis"], "feels_like")
        self.assert_outfit("cold-layers", feels_like_c=5, feels_like_f=90, temp_f=72)
        self.assert_outfit("mild-knit", temperature_c=20, temp_f=90)

    def test_hot_rain_and_storm_precede_heat(self):
        for conditions in (["rain"], ["thunderstorms"], ["showers", "sunny"]):
            with self.subTest(conditions=conditions):
                self.assert_outfit("warm-rain-shell", conditions=conditions, feels_like_c=35)

    def test_76f_dry_current_temperature_selects_no_top(self):
        result = self.assert_outfit("hot-shorts", conditions=["Fair"], temp_f=76)
        self.assertEqual(result["temperature_basis"], "current")
        self.assertEqual(result["rule_order"], 9)
        self.assertEqual(Path(result["asset_path"]).name, "hot-shorts.png")

    def test_exact_hot_dry_boundary_and_just_below(self):
        self.assert_outfit("hot-shorts", conditions=["clear"], temperature_c=24)
        self.assert_outfit("hot-shorts", conditions=["sunny"], temp_f=75.2)
        self.assert_outfit("warm-crochet", conditions=["clear"], temperature_c=23.999)
        self.assert_outfit("warm-crochet", conditions=["sunny"], temp_f=75.19)

    def test_hot_dry_no_top_precedes_wind_and_extreme_heat(self):
        for temperature in (24, 32, 40):
            for condition in ("clear", "sunny", "partly_cloudy", "cloudy", "overcast"):
                with self.subTest(temperature=temperature, condition=condition):
                    self.assert_outfit("hot-shorts", conditions=[condition],
                                       feels_like_c=temperature, wind_kmh=40, gust_kmh=60)

    def test_hot_precipitation_stays_layered_even_with_dry_tag(self):
        self.assert_outfit("warm-rain-shell", conditions=["rain", "sunny"], temp_f=76)
        self.assert_outfit("cold-layers", conditions=["snow", "clear"], temperature_c=24)
        self.assert_outfit("cold-rain-parka", conditions=["sleet", "clear"], temperature_c=24)

    def test_hot_dry_still_prefers_feels_like(self):
        result = self.assert_outfit("warm-crochet", conditions=["clear"], temp_f=76, feels_like_c=23)
        self.assertEqual(result["temperature_basis"], "feels_like")
        self.assert_outfit("hot-shorts", conditions=["clear"], temp_f=72, feels_like_c=24)

    def test_unknown_conditions_preserve_previous_choices(self):
        for conditions in ([], ["weather_unknown"], ["unrecognized"]):
            with self.subTest(conditions=conditions):
                self.assert_outfit("warm-crochet", conditions=conditions, temp_f=76)
                self.assert_outfit("heatwave-linen", conditions=conditions, temperature_c=32)
                self.assert_outfit("hot-wind-stripes", conditions=conditions, temperature_c=24, wind_kmh=20)
                result = self.assert_outfit("mild-knit", conditions=conditions)
                self.assertIsNone(result["rule_order"])

    def test_rain_temperature_boundaries(self):
        for temperature, outfit in ((6.999, "cold-rain-parka"), (7, "rain-shell"),
                                    (21.999, "rain-shell"), (22, "warm-rain-shell")):
            with self.subTest(temperature=temperature):
                self.assert_outfit(outfit, conditions=["drizzle"], temperature_c=temperature)

    def test_icy_precipitation_has_first_priority(self):
        for condition in ("sleet", "freezing_rain", "mixed_rain_snow", "hail"):
            with self.subTest(condition=condition):
                result = self.assert_outfit("cold-rain-parka", conditions=[condition, "snow", "rain"],
                                            feels_like_c=-10, wind_kmh=50)
                self.assertEqual(result["rule_order"], 1)

    def test_snow_boundary(self):
        self.assert_outfit("freezing-puffer", conditions=["snow"], feels_like_c=-0.1)
        self.assert_outfit("cold-layers", conditions=["snow"], feels_like_c=0)

    def test_cold_precedes_wind(self):
        self.assert_outfit("freezing-puffer", feels_like_c=-5, wind_kmh=50)
        self.assert_outfit("cold-layers", feels_like_c=6, gust_kmh=45)
        self.assert_outfit("wind-shell", feels_like_c=7, wind_kmh=20)
        self.assert_outfit("hot-wind-stripes", feels_like_c=22, wind_kmh=20)

    def test_fog_and_wind_priority(self):
        self.assert_outfit("fog-overshirt", conditions=["mist"], temperature_c=7)
        self.assert_outfit("fog-overshirt", conditions=["fog"], temperature_c=21.9)
        self.assert_outfit("warm-crochet", conditions=["fog"], temperature_c=22)
        self.assert_outfit("wind-shell", conditions=["fog"], temperature_c=15, gust_kmh=30)

    def test_missing_temperature_keeps_unbounded_conditions(self):
        for weather, expected, order in (
            ({"conditions": ["rainfall"]}, "rain-shell", 6),
            ({"conditions": ["snow"]}, "cold-layers", 3),
            ({"conditions": ["hail"]}, "cold-rain-parka", 1),
            ({"gust_kmh": 30}, "wind-shell", 11),
            ({"conditions": ["fair"]}, "mild-knit", None),
            ({}, "mild-knit", None),
        ):
            with self.subTest(weather=weather):
                result = self.assert_outfit(expected, **weather)
                self.assertEqual(result["temperature_basis"], "unavailable")
                self.assertEqual(result["rule_order"], order)

    def test_gust_conversion_and_metric_precedence(self):
        self.assert_outfit("hot-wind-stripes", temp_f=72, gust_mph=19)
        self.assert_outfit("warm-crochet", temp_f=72, gust_mph=18)
        self.assert_outfit("warm-crochet", temp_f=72, gust_kmh=0, gust_mph=30)

    def test_null_is_missing_and_sunny_is_supported(self):
        result = self.assert_outfit("warm-crochet", conditions="sunny", feels_like_c=None, temp_f=72)
        self.assertEqual(result["temperature_basis"], "current")

    def test_nonfinite_inputs_rejected_even_when_not_used(self):
        for field in NUMERIC_FIELDS:
            for value in (float("nan"), float("inf"), -float("inf")):
                with self.subTest(field=field, value=value):
                    with self.assertRaisesRegex(ValueError, "finite"):
                        choose_outfit({"temperature_c": 20, field: value})

    def test_invalid_types_and_negative_wind_rejected(self):
        for weather in ([], {"temp_f": True}, {"temp_f": "72"},
                        {"conditions": [2]}, {"conditions": False},
                        {"conditions": {}}, {"wind_kmh": -1}):
            with self.subTest(weather=weather):
                with self.assertRaises(ValueError):
                    choose_outfit(weather)

    def test_missing_asset_fails_instead_of_returning_broken_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copyfile(ASSETS_DIR / "catalog.json", root / "catalog.json")
            shutil.copyfile(ASSETS_DIR / "weather-rules.json", root / "weather-rules.json")
            with self.assertRaisesRegex(FileNotFoundError, "outfit asset does not exist"):
                choose_outfit({"temp_f": 72}, assets_dir=root)

    def test_cli_stdin_and_file(self):
        script = Path(__file__).with_name("choose_outfit.py")
        weather = json.dumps({"conditions": ["fair"], "temp_f": 72})
        with tempfile.TemporaryDirectory() as temporary:
            stdin = subprocess.run([sys.executable, str(script)], input=weather, text=True,
                                   capture_output=True, check=True, cwd=temporary)
            source = Path(temporary) / "weather.json"
            source.write_text(weather, encoding="utf-8")
            filename = subprocess.run([sys.executable, str(script), str(source)], text=True,
                                      capture_output=True, check=True, cwd=temporary)
        self.assertEqual(json.loads(stdin.stdout), json.loads(filename.stdout))
        self.assertEqual(json.loads(stdin.stdout)["outfit_id"], "warm-crochet")

    def test_explicit_asset_directory_can_travel_with_copied_script(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copytree(ASSETS_DIR, root / "weather")
            script = root / "choose_outfit.py"
            shutil.copyfile(Path(__file__).with_name("choose_outfit.py"), script)
            result = subprocess.run(
                [sys.executable, str(script), "--assets-dir", str(root / "weather")],
                input='{"conditions":"rain","temperature_c":12}', text=True,
                capture_output=True, check=True, cwd=root)
            selection = json.loads(result.stdout)
            self.assertEqual(selection["outfit_id"], "rain-shell")
            self.assertEqual(Path(selection["asset_path"]), (root / "weather/rain-shell.png").resolve())


if __name__ == "__main__":
    unittest.main()
