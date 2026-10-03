#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Drive the actual input handler with timed wheel edges and fake device state.

The regression is observable side effects: camera gestures must create zero
PTT events, while a held wheel still records and special presses stay immediate.
"""
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

FAKES = r'''
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include "muse_wheel_gesture.h"
#define CONFIG_MUSE_WATCHER_CAMERA 1
#define MUSE_BTN_TALK_PRESS 1u
#define MUSE_BTN_TALK_RELEASE 2u
#define MUSE_BTN_WHEEL_PREV 16u
#define MUSE_BTN_WHEEL_NEXT 32u
#define MUSE_LINK_CONFIRM 1
#define MUSE_MODE_IDLE 1
#define WATCHER_CAMERA_CLOSED 0
#define MUSE_PTT_DOWN 1
#define MUSE_PTT_UP 2
#define MUSE_MENU_SELECT 1
#define ESP_LOGI(...) ((void)0)
static bool s_talk_down;
static int camera, asleep, menu, link;
static unsigned long long now;
static struct { const char *talk_button; } board = {"wheel"}, *muse_board = &board;
static int watcher_camera_state(void) { return camera; }
static bool muse_state_asleep(void) { return asleep; }
static bool muse_menu_is_open(void) { return menu; }
static int muse_link_state(void) { return link; }
static int64_t esp_timer_get_time(void) { return (int64_t)now * 1000; }
static void muse_state_poke(void) {}
static void muse_ui_wheel_click(void) { puts("click"); }
static void watcher_camera_preview_toggle(void) { puts("camera"); camera = 1; }
static bool muse_link_talk_press(void) {
    if (link) { puts("pair"); link = 0; return true; } return false;
}
static void muse_menu_key(int key) { (void)key; puts("menu"); menu = 0; }
static void set_asleep(bool value, const char *why) {
    (void)why; asleep = value; puts("wake");
}
static void post(int event, bool wake) {
    printf("%s%s\n", event == MUSE_PTT_DOWN ? "down" : "up", wake ? " wake" : "");
}
'''

MAIN = r'''
int main(void) {
    unsigned edges;
    while (scanf("%llu %u %d %d %d %d", &now, &edges, &camera, &asleep, &menu, &link) == 6)
        talk_button(edges);
    return 0;
}
'''


class WheelGestureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cc = shlex.split(os.environ.get("CC", "cc"))
        if not cc or not shutil.which(cc[0]):
            raise unittest.SkipTest("C compiler unavailable")
        cls.temp = tempfile.TemporaryDirectory()
        source = (ROOT / "components/muse/muse_input.c").read_text()
        start = source.index("static void talk_button(unsigned ev)")
        end = source.index("/* A pairing prompt wakes", start)
        harness = Path(cls.temp.name) / "wheel.c"
        harness.write_text(FAKES + source[start:end] + MAIN)
        cls.binary = Path(cls.temp.name) / "wheel"
        result = subprocess.run([*cc, "-std=c11", "-Wall", "-Wextra", "-Werror",
                                 "-I", str(ROOT / "components/muse"), str(harness),
                                 "-o", str(cls.binary)], capture_output=True, text=True)
        if result.returncode: raise AssertionError(result.stdout + result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def run_edges(self, steps):
        # timestamp, edge bits, camera open, asleep, menu open, pairing pending
        data = "".join(" ".join(map(str, (*row, *([0] * (6 - len(row)))))) + "\n" for row in steps)
        result = subprocess.run([str(self.binary)], input=data, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.splitlines()

    def test_double_click_never_starts_recording_even_with_slow_first_click(self):
        for held in (0, 50, 180, 200, 250, 299):
            with self.subTest(first_click_ms=held):
                self.assertEqual(self.run_edges([(100, 1), (100 + held, 2),
                    (200 + held, 1), (250 + held, 2, 1), (900, 0, 1)]), ["camera"])

    def test_continuous_hold_starts_once_at_deadline_and_releases(self):
        self.assertEqual(self.run_edges([(10, 1), (200, 0), (309, 0), (310, 0),
                                        (600, 0), (1000, 2)]), ["down", "up"])

    def test_release_before_hold_deadline_never_records(self):
        self.assertEqual(self.run_edges([(10, 1), (309, 2), (660, 0)]), ["click"])

    def test_long_hold_does_not_arm_a_camera_click(self):
        self.assertEqual(self.run_edges([(10, 1), (310, 0), (600, 2),
                                        (650, 1), (950, 0), (1300, 2)]), ["down", "up", "down", "up"])

    def test_single_tap_waits_for_second_click_then_pets(self):
        self.assertEqual(self.run_edges([(10, 1), (60, 2), (410, 0), (411, 0), (900, 0)]), ["click"])

    def test_camera_wheel_hold_and_double_click_never_record(self):
        self.assertEqual(self.run_edges([(10, 1, 1), (1000, 0, 1), (1100, 2, 1),
                                        (1300, 1, 1), (1350, 2, 1), (1450, 1, 1),
                                        (1500, 2, 1)]), ["camera"])

    def test_opening_camera_during_hold_releases_audio_and_close_does_not_restart(self):
        self.assertEqual(self.run_edges([(10, 1), (310, 0), (400, 0, 1),
                                        (600, 0, 0), (1000, 0), (1100, 2)]), ["down", "up"])

    def test_latched_tap_edges_are_not_lost_or_recorded(self):
        self.assertEqual(self.run_edges([(10, 3), (100, 3), (200, 0, 1)]), ["camera"])

    def test_tick_wrap_preserves_hold_and_double_click(self):
        base = 2**32 - 100
        self.assertEqual(self.run_edges([(base, 1), (base + 300, 0), (base + 900, 2)]), ["down", "up"])
        self.assertEqual(self.run_edges([(base, 1), (base + 50, 2),
                                        (base + 150, 1), (base + 200, 2, 1)]), ["camera"])

    def test_wake_is_immediate_and_release_stays_on_wake_path(self):
        self.assertEqual(self.run_edges([(10, 1, 0, 1), (20, 0), (50, 2)]), ["wake", "down wake", "up"])

    def test_rotation_after_click_cancels_pending_confirmation(self):
        # Click Cancel, rotate to Send before the delayed tap resolves.
        self.assertEqual(self.run_edges([(10,1,1),(60,2,1),(160,32,1),(420,0,1)]), [])

    def test_rotation_while_pressed_cancels_click_and_recording(self):
        self.assertEqual(self.run_edges([(10,1,1),(50,16,1),(60,2,1),(420,0,1)]), [])
        self.assertEqual(self.run_edges([(10,1,1),(60,34,1),(420,0,1)]), [])
        self.assertEqual(self.run_edges([(10,35,1),(420,0,1)]), [])
        self.assertEqual(self.run_edges([(10,1),(50,32),(310,0),(400,2),(800,0)]), [])

    def test_menu_and_pairing_stay_immediate_without_voice_or_camera(self):
        self.assertEqual(self.run_edges([(10, 1, 0, 0, 1), (50, 2), (100, 1), (150, 2)]), ["menu"])
        self.assertEqual(self.run_edges([(10, 1, 0, 0, 0, 1), (50, 2), (100, 1), (150, 2)]), ["pair"])


if __name__ == "__main__":
    unittest.main()
