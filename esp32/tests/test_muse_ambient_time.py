"""The production avatar follows configured local time, including DST."""
import ctypes
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

ESP32 = Path(__file__).resolve().parents[1]


@unittest.skipUnless(hasattr(time, "tzset"), "POSIX timezone support required")
class AmbientTimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        lib = Path(cls.temporary.name) / "ambient.so"
        subprocess.run([os.environ.get("CC", "cc"), "-std=c11", "-D_POSIX_C_SOURCE=200809L",
                        "-shared", "-fPIC", "-I" + str(ESP32 / "components/muse"),
                        str(ESP32 / "avatar/muse_pixel.c"), "-lm", "-o", str(lib)], check=True)
        cls.lib = ctypes.CDLL(str(lib))
        cls.lib.muse_ambient_period_at.argtypes = [ctypes.c_long]
        cls.lib.muse_ambient_period_at.restype = ctypes.c_int

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def setUp(self):
        self.old_tz = os.environ.get("TZ")

    def tearDown(self):
        if self.old_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self.old_tz
        time.tzset()

    def period(self, tz, date):
        os.environ["TZ"] = tz
        time.tzset()
        epoch = int(datetime.fromisoformat(date).replace(tzinfo=timezone.utc).timestamp())
        return self.lib.muse_ambient_period_at(epoch)

    def test_invalid_clock_has_no_scene(self):
        self.assertEqual(self.lib.muse_ambient_period_at(0), 0)

    def test_period_boundaries_in_utc(self):
        for hour, expected in ((4, 4), (5, 1), (10, 1), (11, 2), (16, 2), (17, 3), (20, 3), (21, 4)):
            self.assertEqual(self.period("UTC0", f"2026-06-01T{hour:02}:00:00"), expected)

    def test_non_pacific_time_zone(self):
        self.assertEqual(self.period("JST-9", "2026-06-01T00:00:00"), 1)
        self.assertEqual(self.period("UTC0", "2026-06-01T00:00:00"), 4)

    def test_posix_daylight_saving_changes_the_hour(self):
        zone = "PST8PDT,M3.2.0,M11.1.0"
        self.assertEqual(self.period(zone, "2026-01-01T12:00:00"), 4)  # 04:00
        self.assertEqual(self.period(zone, "2026-07-01T12:00:00"), 1)  # 05:00


if __name__ == "__main__":
    unittest.main()
