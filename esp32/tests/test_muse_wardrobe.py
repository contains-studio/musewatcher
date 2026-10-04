"""Compile and exercise the production wardrobe renderer; no ESP-IDF or device."""
import ctypes
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest

ESP32 = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("wardrobe_preview", ESP32 / "tools/muse/wardrobe_preview.py")
preview = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preview)


class WardrobeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.lib = preview.build_renderer(cls.temporary.name)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def setUp(self):
        self.lib.muse_wardrobe_select(b"default")
        self.lib.muse_wardrobe_render(None, False)

    def frame(self, outfit="hot-shorts", t=0, mode=1, size=96, level=0, happy=0, ambient=True):
        return list(preview.render_pixels(self.lib, outfit, preview.Pose(mode, t, t, level, happy), size, ambient))

    def test_default_and_invalid_selection_preserve_state(self):
        self.assertEqual(self.lib.muse_wardrobe_current(), b"default")
        self.assertTrue(self.lib.muse_wardrobe_valid(b"default"))
        self.assertFalse(self.lib.muse_wardrobe_render(None, False))
        output = (ctypes.c_uint16 * 4)(*[0xBEEF] * 4)
        self.assertFalse(self.lib.muse_wardrobe_scale(output, 2, 0, 1, 0, 1, 96))
        self.assertEqual(list(output), [0xBEEF] * 4)
        self.assertTrue(self.lib.muse_wardrobe_select(b"hot-shorts"))
        for invalid in (None, b"", b"HOT-SHORTS", b"not-an-outfit", b"hot-shorts "):
            with self.subTest(invalid=invalid):
                self.assertFalse(self.lib.muse_wardrobe_valid(invalid))
                self.assertFalse(self.lib.muse_wardrobe_select(invalid))
                self.assertEqual(self.lib.muse_wardrobe_current(), b"hot-shorts")

    def test_all_thirteen_outfits_distinct_nonempty_and_low(self):
        frames = set()
        for outfit in preview.OUTFITS:
            with self.subTest(outfit=outfit):
                self.assertTrue(self.lib.muse_wardrobe_valid(outfit.encode()))
                frame = self.frame(outfit)
                occupied = [i for i, value in enumerate(frame) if value]
                self.assertGreater(len(occupied), 1500)
                self.assertGreaterEqual(min(i // 96 for i in occupied), 15)
                self.assertGreaterEqual(max(i // 96 for i in occupied), 87)
                self.assertLessEqual(max(i // 96 for i in occupied), 93)
                frames.add(tuple(frame))
        self.assertEqual(len(frames), 13)

    def test_selection_changes_only_at_render_boundary(self):
        original = self.frame("warm-crochet", size=320)
        top = (ctypes.c_uint16 * (320 * 160))()
        bottom = (ctypes.c_uint16 * (320 * 160))()
        self.assertTrue(self.lib.muse_wardrobe_scale(top, 320, 0, 319, 0, 159, 320))
        self.lib.muse_wardrobe_select(b"hot-shorts")
        self.assertTrue(self.lib.muse_wardrobe_scale(bottom, 320, 0, 319, 160, 319, 320))
        self.assertEqual(list(top) + list(bottom), original)
        self.assertNotEqual(self.frame("hot-shorts", size=320), original)
        self.lib.muse_wardrobe_select(b"default")
        self.assertTrue(self.lib.muse_wardrobe_scale(top, 320, 0, 319, 0, 159, 320))
        self.assertFalse(self.lib.muse_wardrobe_render(None, False))
        self.assertFalse(self.lib.muse_wardrobe_scale(top, 320, 0, 319, 0, 159, 320))

    def test_all_modes_animate_without_changing_outfit(self):
        for mode in range(7):
            with self.subTest(mode=mode):
                frames = {tuple(self.frame(t=t, mode=mode, level=0.6, happy=0.3)) for t in (0, 0.9, 1.8)}
                self.assertGreater(len(frames), 1)
                self.assertTrue(all(any(frame) for frame in frames))
                self.assertEqual(self.lib.muse_wardrobe_current(), b"hot-shorts")

    def test_pose_level_and_happy_change_motion(self):
        self.assertNotEqual(self.frame(mode=4, level=0), self.frame(mode=4, level=1))
        # Hearts outside the body must not stand in for an excited expression.
        # At t=0 both poses are grounded: inspect the hot-shorts face itself.
        idle, excited = self.frame(t=0, happy=0), self.frame(t=0, happy=1)
        face = [y * 96 + x for y in range(36, 49) for x in range(35, 61)]
        self.assertNotEqual([idle[i] for i in face], [excited[i] for i in face],
                            "A tap must change Muse's face, not just add hearts")
        # The grounded pose cannot satisfy this by moving the whole sprite.
        clothes = [y * 96 + x for y in range(70, 88) for x in range(36, 59)]
        self.assertEqual([idle[i] for i in clothes], [excited[i] for i in clothes])
        self.assertTrue(any(self.frame(t=float("nan"), level=float("inf"), happy=-1)))

    def test_every_outfit_reacts_then_returns_to_the_same_idle_art(self):
        face = [y * 96 + x for y in range(36, 49) for x in range(35, 61)]
        clothes = [y * 96 + x for y in range(75, 89) for x in range(38, 57)]
        for outfit in preview.OUTFITS:
            with self.subTest(outfit=outfit):
                idle = self.frame(outfit, t=0)
                excited = self.frame(outfit, t=0, happy=1)
                self.assertNotEqual([idle[i] for i in face], [excited[i] for i in face])
                self.assertEqual([idle[i] for i in clothes], [excited[i] for i in clothes])
                self.assertEqual(self.lib.muse_wardrobe_current(), outfit.encode())
                self.assertEqual(self.frame(outfit, t=0, happy=0), idle)

    def test_idle_has_animated_pixels_around_the_dressed_character(self):
        margins = [y * 96 + x for y in range(15, 86) for x in (*range(4, 17), *range(80, 92))]
        frames = [self.frame(t=t) for t in (0, 0.6, 1.4)]
        for frame in frames:
            self.assertGreater(sum(bool(frame[i]) for i in margins), 30)
        self.assertGreater(len({tuple(frame[i] for i in margins) for frame in frames}), 1)

    def test_scenery_is_quiet_during_other_states_and_reading(self):
        margins = [y * 96 + x for y in range(15, 86) for x in (*range(4, 17), *range(80, 92))]
        for mode in (0, 2, 3, 4, 5, 6):
            frame = self.frame(t=1.2, mode=mode)
            self.assertFalse(any(frame[i] for i in margins), mode)
        frame = self.frame(t=1.2, ambient=False)
        self.assertFalse(any(frame[i] for i in margins))
        # A stale tap must not raise arms or replace facial expressions over a
        # reply/settings/active voice state. At zero phase the old hop is zero.
        for mode, ambient in ((1, False), (2, True), (3, True), (4, True), (5, True)):
            self.assertEqual(self.frame(mode=mode, ambient=ambient, happy=1),
                             self.frame(mode=mode, ambient=ambient, happy=0))

    def test_weather_outfits_have_distinct_moving_scenery(self):
        margins = [y * 96 + x for y in range(15, 86) for x in (*range(4, 17), *range(80, 92))]
        scenes = set()
        for outfit in ("hot-shorts", "rain-shell", "freezing-puffer", "wind-shell", "fog-overshirt"):
            rendered = [self.frame(outfit, t=t) for t in (0, 0.7)]
            frames = [tuple(frame[i] for i in margins) for frame in rendered]
            self.assertNotEqual(*frames, outfit)
            self.assertTrue(any(frames[0]), outfit)
            scenes.add(frames[0])
        self.assertEqual(len(scenes), 5)

    def test_clipping_padding_and_edge_coordinates(self):
        self.frame()
        width, height, stride = 104, 104, 107
        output = (ctypes.c_uint16 * (height * stride + 2))(*[0xBEEF] * (height * stride + 2))
        start = ctypes.cast(ctypes.byref(output, 2), ctypes.POINTER(ctypes.c_uint16))
        self.assertTrue(self.lib.muse_wardrobe_scale(start, stride, -4, 99, -4, 99, 96))
        self.assertEqual(output[0], 0xBEEF)
        self.assertEqual(output[-1], 0xBEEF)
        for y in range(height):
            for x in range(width):
                if x < 4 or x >= 100 or y < 4 or y >= 100:
                    self.assertEqual(output[1 + y * stride + x], 0)
            self.assertEqual(list(output[1 + y * stride + width:1 + (y + 1) * stride]), [0xBEEF] * 3)
        for value in (-2147483648, 2147483647):
            single = (ctypes.c_uint16 * 1)(0xBEEF)
            self.assertTrue(self.lib.muse_wardrobe_scale(single, 1, value, value, value, value, 96))
            self.assertEqual(single[0], 0)

    def test_invalid_scale_arguments_leave_destination_untouched(self):
        self.frame()
        output = (ctypes.c_uint16 * 4)(*[0xBEEF] * 4)
        for args in ((2, 0, 1, 0, 1, 0), (2, 0, 1, 0, 1, 513),
                     (1, 0, 1, 0, 1, 96), (2, 1, 0, 0, 1, 96),
                     (2, 0, 1, 1, 0, 96), (513, 0, 512, 0, 0, 96)):
            with self.subTest(args=args):
                self.assertFalse(self.lib.muse_wardrobe_scale(output, *args))
                self.assertEqual(list(output), [0xBEEF] * 4)
        self.assertFalse(self.lib.muse_wardrobe_scale(None, 2, 0, 1, 0, 1, 96))

    def test_strip_output_matches_full_frame_at_ui_sizes(self):
        for outfit, size, happy in ((outfit, size, happy) for outfit in preview.OUTFITS
                                     for size in (256, 320, 512) for happy in (0, 1)):
            with self.subTest(outfit=outfit, size=size, happy=happy):
                full = self.frame(outfit, size=size, t=1.3, happy=happy)
                stripes = []
                for y in range(0, size, 16):
                    end = min(y + 15, size - 1)
                    output = (ctypes.c_uint16 * ((end - y + 1) * size))()
                    self.assertTrue(self.lib.muse_wardrobe_scale(output, size, 0, size - 1, y, end, size))
                    stripes.extend(output)
                self.assertEqual(stripes, full)

    def test_concurrent_selection_does_not_change_latched_frame(self):
        expected = self.frame(happy=1)
        def switch():
            for i in range(2000):
                self.lib.muse_wardrobe_select(preview.OUTFITS[i % 13].encode())
        worker = threading.Thread(target=switch)
        worker.start()
        try:
            for _ in range(50):
                output = (ctypes.c_uint16 * (96 * 96))()
                self.assertTrue(self.lib.muse_wardrobe_scale(output, 96, 0, 95, 0, 95, 96))
                self.assertEqual(list(output), expected)
        finally:
            worker.join()


class WardrobeExpressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.binary = Path(cls.temporary.name) / "wardrobe-expression"
        subprocess.run([os.environ.get("CC", "cc"), "-std=c11", "-O1", "-g",
                        "-Wall", "-Wextra", "-Werror", "-fsanitize=address,undefined",
                        "-fno-omit-frame-pointer",
                        str(ESP32 / "tests/muse_wardrobe_expression_harness.c"),
                        "-lm", "-o", str(cls.binary)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_facial_fill_preserves_smooth_color_ramps(self):
        subprocess.run([str(self.binary), "fill"], check=True)

    def test_expression_anchors_cover_original_marks_and_preserve_surrounding_fur(self):
        subprocess.run([str(self.binary), "anchors"], check=True)

    def test_rotated_sampling_stays_inside_each_sprite(self):
        subprocess.run([str(self.binary), "bounds"], check=True)


if __name__ == "__main__":
    unittest.main()
