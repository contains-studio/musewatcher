"""Weather-card behavior and file-format checks; all inputs are local fixtures."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("weather_card", ROOT / "tools/weather_card.py")
card = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(card)


class WeatherCardTests(unittest.TestCase):
    def test_sample_cli_creates_baseline_jpeg_and_reports_matching_outfit(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "card.jpg"
            result = subprocess.run(
                [sys.executable, str(ROOT / "tools/weather_card.py"),
                 str(ROOT / "examples/weather.json"), "--output", str(output)],
                cwd=temporary, text=True, capture_output=True, check=True)
            metadata = json.loads(result.stdout)
            self.assertEqual(metadata["outfit_id"], "hot-shorts")
            self.assertEqual(metadata["temperature_basis"], "feels_like")
            self.assertEqual(metadata["display_lines"][0]["text"], "78°F")
            with Image.open(output) as image:
                self.assertEqual(image.format, "JPEG")
                self.assertEqual(image.size, (412, 412))
                self.assertEqual(image.mode, "RGB")
                self.assertFalse(image.info.get("progressive", False))
                self.assertEqual(image.getpixel((0, 0)), (0, 0, 0))
                self.assertIsNotNone(image.crop((90, 25, 322, 180)).getbbox())
                self.assertIsNotNone(image.crop((90, 190, 322, 395)).getbbox())

    def test_rain_uses_shell_even_on_a_hot_day(self):
        image, metadata = card.render_card({"conditions": ["rain", "clear"], "temp_f": 90})
        self.assertEqual(metadata["outfit_id"], "warm-rain-shell")
        self.assertEqual(metadata["display_lines"], [{"field": "current_temperature", "text": "90°F"}])
        self.assertEqual(image.getpixel((206, 30)), (0, 0, 0))

    def test_missing_numbers_are_omitted_and_zero_is_not_missing(self):
        self.assertEqual(card.display_lines({}), [])
        self.assertEqual(card.display_lines({"humidity_pct": 0, "wind_mph": 0}), [
            {"field": "wind", "text": "Wind 0 mph"},
            {"field": "humidity", "text": "Humidity 0%"},
        ])
        self.assertEqual(card.display_lines({"high_f": 65, "humidity_pct": None}), [
            {"field": "high_low", "text": "H 65°"},
        ])

    def test_metric_inputs_convert_and_feels_like_is_labeled_when_current_missing(self):
        self.assertEqual(card.display_lines({"temperature_c": 20, "temp_f": 90, "wind_kmh": 16.09344})[:2], [
            {"field": "current_temperature", "text": "68°F"},
            {"field": "wind", "text": "Wind 10 mph"},
        ])
        self.assertEqual(card.display_lines({"feels_like_c": 20}), [
            {"field": "feels_like", "text": "Feels 68°F"},
        ])

    def test_bad_values_fail_before_rendering(self):
        for weather in (
            [], {"temp_f": True}, {"temp_f": "78"}, {"temp_f": float("nan")},
            {"high_f": float("inf")}, {"low_f": -1000}, {"temp_f": 10**1000},
            {"humidity_pct": 101}, {"humidity_pct": -1}, {"wind_mph": -1},
            {"wind_kmh": 1000}, {"gust_mph": float("nan")},
            {"high_f": 40, "low_f": 60}, {"conditions": ["a" * 65]},
            {"conditions": ["rain"] * 65}, {"conditions": [True]},
        ):
            with self.subTest(weather=weather):
                with self.assertRaises(ValueError):
                    card.render_card(weather)

    def test_cli_rejects_oversize_json_without_creating_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, output = Path(temporary) / "large.json", Path(temporary) / "card.jpg"
            source.write_text(json.dumps({"ignored": "x" * 65536}))
            result = subprocess.run(
                [sys.executable, str(ROOT / "tools/weather_card.py"), str(source), "--output", str(output)],
                text=True, capture_output=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn("exceeds 64 KiB", result.stderr)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
