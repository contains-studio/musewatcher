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

"""Exercise the Watcher's production encoder polling with signed PCNT samples."""

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
#include <stdio.h>
#include <stdlib.h>
#define ESP_OK 0
static bool s_paused, s_wheel_moved;
static int s_knob, count, read_error;
static unsigned push;
static int pcnt_unit_get_count(int unit, int *out) {
    (void)unit;
    if (read_error) return read_error;
    *out = count;
    return ESP_OK;
}
static int pcnt_unit_clear_count(int unit) { (void)unit; count = 0; return ESP_OK; }
static unsigned poll_wheel_push(void) { return push; }
'''

MAIN = r'''
int main(void) {
    int paused, moved;
    while (scanf("%d %d %d %u %d", &count, &paused, &moved, &push, &read_error) == 5) {
        s_paused = paused;
        s_wheel_moved |= moved;
        printf("%u\n", poll_buttons());
    }
    return 0;
}
'''


class WheelEncoderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cc = shlex.split(os.environ.get("CC", "cc"))
        if not cc or not shutil.which(cc[0]):
            raise unittest.SkipTest("C compiler unavailable")
        cls.temp = tempfile.TemporaryDirectory()
        source = (ROOT / "components/muse/boards/board_sensecap_watcher.c").read_text()
        header = (ROOT / "components/muse/muse_board.h").read_text()
        defines = "\n".join(line for line in (source + header).splitlines()
                            if line.startswith(("#define TURN_COUNTS ", "#define MUSE_BTN_")))
        start = source.index("static unsigned poll_wheel_turn(void)")
        end = source.index("/*\n * Display paused:", start)
        harness = Path(cls.temp.name) / "encoder.c"
        harness.write_text(FAKES + defines + "\n" + source[start:end] + MAIN)
        cls.binary = Path(cls.temp.name) / "encoder"
        result = subprocess.run([*cc, "-std=c11", "-Wall", "-Wextra", "-Werror",
                                 str(harness), "-o", str(cls.binary)], capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def poll(self, rows):
        # signed quarter steps, paused, wake interrupt, push edges, PCNT error
        data = "".join(" ".join(map(str, (*row, *([0] * (5 - len(row)))))) + "\n"
                       for row in rows)
        result = subprocess.run([str(self.binary)], input=data, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return [int(line) for line in result.stdout.splitlines()]

    def test_forward_and_backward_detents_have_distinct_events(self):
        self.assertEqual(self.poll([(1,), (0,), (1,), (0,), (-1,), (-1,)]),
                         [0, 0, 32, 0, 0, 16])

    def test_fast_turn_drains_one_detent_per_poll_without_losing_residual(self):
        self.assertEqual(self.poll([(11,), (0,), (0,), (0,), (0,), (0,), (1,)]),
                         [32, 32, 32, 32, 32, 0, 32])
        self.assertEqual(self.poll([(-6,), (0,), (0,), (0,)]), [16, 16, 16, 0])

    def test_direction_reversal_cancels_incomplete_steps(self):
        self.assertEqual(self.poll([(1,), (-1,), (-2,), (2,)]), [0, 0, 16, 32])
        self.assertEqual(self.poll([(5,), (-3,), (-2,)]), [32, 0, 16])

    def test_paused_wake_is_once_and_discards_pending_navigation(self):
        self.assertEqual(self.poll([(5,), (0, 1, 1), (0, 1, 1), (0, 1), (0,), (2,)]),
                         [32, 32, 0, 0, 0, 32])

    def test_failed_counter_read_does_not_create_a_step(self):
        self.assertEqual(self.poll([(1,), (100, 0, 0, 0, 1), (1,)]), [0, 0, 32])

    def test_push_edges_are_preserved_and_turns_never_generate_aux(self):
        events = self.poll([(2, 0, 0, 1), (-2, 0, 0, 2), (0, 0, 0, 3)])
        self.assertEqual(events, [33, 18, 3])
        self.assertTrue(all(event & 12 == 0 for event in events))


if __name__ == "__main__":
    unittest.main()
