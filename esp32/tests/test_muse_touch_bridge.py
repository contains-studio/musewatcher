#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
# Licensed under the Apache License, Version 2.0.

"""Drive the production touch/wheel input bridge and voice queue reader.

No UI recognizer is mocked into a desired outcome: real producer functions,
input polling, source reduction, and voice edge consumption are compiled here.
"""
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
MUSE = ROOT / "components/muse"


def function(source, name):
    m = re.search(r"^(?:static )?(?:bool|void|esp_err_t) " + name + r"\([^;]*?\)\n\{", source, re.M)
    if not m:
        raise AssertionError("missing " + name)
    start = source.index("{", m.start())
    end, depth = start + 1, 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[m.start():end]


FAKES = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdatomic.h>
#include "muse_ptt_sources.h"
#define CONFIG_MUSE_BOARD_SENSECAP_WATCHER 1
#define CONFIG_MUSE_WATCHER_CAMERA 1
#define pdTRUE 1
#define pdPASS 1
#define ESP_OK 0
#define ESP_FAIL -1
#define ESP_ERR_NO_MEM -2
#define ESP_LOGI(...) ((void)0)
#define ESP_LOGW(...) ((void)0)
#define WATCHER_CAMERA_CLOSED 0
#define TAG "test"
typedef int esp_err_t;
typedef enum { MUSE_PTT_DOWN, MUSE_PTT_UP, MUSE_PTT_CANCEL } muse_ptt_t;
typedef struct { muse_ptt_t type; bool wake; } muse_input_event_t;
typedef struct { unsigned char data[64][32]; size_t size; unsigned head, n, cap; } queue_t;
typedef queue_t *QueueHandle_t;
typedef void *TaskHandle_t;
static queue_t storage[4];
static unsigned queues;
static QueueHandle_t s_queue;
static TaskHandle_t s_input;
static bool s_record_cancelled;
static int blocked, asleep, camera;
static QueueHandle_t xQueueCreate(unsigned n, unsigned size) {
    assert(queues < 4 && n <= 64 && size <= 32);
    queue_t *q = &storage[queues++]; q->cap = n; q->size = size; return q;
}
static int xQueueSend(QueueHandle_t q, const void *item, int wait) {
    assert(wait == 0);
    if ((q == s_queue && blocked) || q->n == q->cap) return 0;
    memcpy(q->data[(q->head + q->n) % q->cap], item, q->size); q->n++; return pdTRUE;
}
static int xQueuePeek(QueueHandle_t q, void *out, int wait) {
    assert(wait == 0);
    if (!q->n) return 0;
    memcpy(out, q->data[q->head], q->size); return pdTRUE;
}
static int xQueueReceive(QueueHandle_t q, void *out, int wait) {
    if (!xQueuePeek(q, out, wait)) return 0;
    q->head = (q->head + 1) % q->cap; q->n--; return pdTRUE;
}
static int xQueueReset(QueueHandle_t q) { q->n = q->head = 0; return pdTRUE; }
static void vQueueDelete(QueueHandle_t q) { (void)q; }
static bool muse_state_asleep(void) { return asleep; }
static int watcher_camera_state(void) { return camera; }
static void muse_state_poke(void) {}
static void input_task(void *arg) { (void)arg; }
static void serial_task(void *arg) { (void)arg; }
static int xTaskCreate(void (*fn)(void *), const char *name, int stack, void *arg,
                       int priority, TaskHandle_t *out) {
    (void)fn; (void)name; (void)stack; (void)arg; (void)priority; (void)out; return pdPASS;
}
'''

MAIN = r'''
int main(void) {
    s_queue = xQueueCreate(32, sizeof(muse_input_event_t));
    assert(muse_input_start(s_queue) == ESP_OK);
    char command;
    int value;
    while (scanf(" %c %d", &command, &value) == 2) {
        switch (command) {
        case 'T': muse_input_touch(value); break;
        case 'W': post(value, false); break;
        case 'S': post_source(MUSE_PTT_SERIAL, value, false); break;
        case 'H': post(MUSE_PTT_DOWN, true); break;
        case 'P': poll_touch(); break;
        case 'B': blocked = value; break;
        case 'A': asleep = value; break;
        case 'C': camera = value; break;
        case 'I': {
            muse_input_event_t e = {.type = value}; assert(xQueueSend(s_queue, &e, 0)); break;
        }
        case 'G': printf("got%d=%d\n", value, got_event(value)); break;
        case 'D': {
            muse_input_event_t e;
            while (xQueueReceive(s_queue, &e, 0)) printf("%s%s\n",
                e.type == MUSE_PTT_DOWN ? "down" : e.type == MUSE_PTT_UP ? "up" : "cancel",
                e.wake ? " wake" : "");
            break;
        }
        default: abort();
        }
    }
    return 0;
}
'''


class TouchBridgeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cc = shlex.split(os.environ.get("CC", "cc"))
        if not cc or not shutil.which(cc[0]):
            raise unittest.SkipTest("C compiler unavailable")
        cls.temp = tempfile.TemporaryDirectory()
        source = (MUSE / "muse_input.c").read_text()
        start = source.index("#if CONFIG_MUSE_BOARD_SENSECAP_WATCHER", source.index("static volatile bool s_nap_now"))
        end = source.index("static bool update_power(void);", start)
        voice = (MUSE / "muse_voice.c").read_text()
        cls.binaries = {}
        for board in ("watcher", "legacy"):
            reader_config = "" if board == "watcher" else "\n#undef CONFIG_MUSE_BOARD_SENSECAP_WATCHER\n#define CONFIG_MUSE_BOARD_SENSECAP_WATCHER 0\n"
            harness = Path(cls.temp.name) / (board + ".c")
            harness.write_text(FAKES + source[start:end] + function(source, "muse_input_start")
                               + reader_config + function(voice, "got_event") + MAIN)
            binary = Path(cls.temp.name) / board
            p = subprocess.run([*cc, "-std=c11", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function",
                "-fsanitize=address,undefined", "-I", str(MUSE), str(harness), "-o", str(binary)],
                capture_output=True, text=True)
            if p.returncode:
                raise AssertionError(p.stdout + p.stderr)
            cls.binaries[board] = binary

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def run_steps(self, steps, board="watcher"):
        p = subprocess.run([str(self.binaries[board])], input="".join(f"{c} {v}\n" for c, v in steps),
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout.splitlines()

    def test_down_and_up_before_one_poll_are_both_delivered(self):
        self.assertEqual(self.run_steps([('T',0),('T',1),('P',0),('D',0)]), ['down','up'])

    def test_cancellation_followed_by_new_press_before_poll_is_preserved(self):
        self.assertEqual(self.run_steps([('T',0),('P',0),('D',0),('T',2),('T',0),('P',0),('D',0)]),
                         ['down','cancel','down'])

    def test_touch_down_before_wheel_release_in_same_poll_does_not_end_session(self):
        self.assertEqual(self.run_steps([('W',0),('P',0),('D',0),('T',0),('W',1),('P',0),('D',0),
                                         ('T',1),('P',0),('D',0)]), ['down','up'])

    def test_wheel_down_before_touch_release_in_same_poll_does_not_end_session(self):
        self.assertEqual(self.run_steps([('T',0),('P',0),('D',0),('W',0),('T',1),('P',0),('D',0),
                                         ('W',1),('P',0),('D',0)]), ['down','up'])

    def test_serial_down_up_and_repress_remain_ordered(self):
        self.assertEqual(self.run_steps([('S',0),('S',1),('S',0),('P',0),('D',0)]),
                         ['down','up','down'])

    def test_serial_overlap_prevents_touch_cancellation_from_ending_session(self):
        self.assertEqual(self.run_steps([('T',0),('P',0),('S',0),('T',2),('P',0),('D',0),
                                         ('S',1),('P',0),('D',0)]), ['down','up'])

    def test_voice_queue_full_keeps_both_touch_edges_until_retry(self):
        self.assertEqual(self.run_steps([('B',1),('T',0),('T',1),('P',0),('D',0),
                                         ('B',0),('P',0),('D',0)]), ['down','up'])

    def test_voice_queue_full_preserves_cancel_before_next_down(self):
        self.assertEqual(self.run_steps([('T',0),('P',0),('D',0),('B',1),('T',2),('T',0),
                                         ('P',0),('B',0),('P',0),('D',0)]), ['down','cancel','down'])

    def test_input_overflow_cancels_active_recording_and_allows_next_gesture(self):
        steps = [('T',0),('P',0),('D',0)] + [('T',i % 2) for i in range(20)]
        steps += [('P',0),('D',0),('T',0),('T',1),('P',0),('D',0)]
        self.assertEqual(self.run_steps(steps), ['down','cancel','down','up'])

    def test_input_overflow_before_voice_accepts_down_cannot_start_stale_recording(self):
        steps = [('B',1),('T',0),('P',0)] + [('T',i % 2) for i in range(20)]
        steps += [('P',0),('B',0),('P',0),('D',0)]
        self.assertEqual(self.run_steps(steps), [])

    def test_sleep_or_camera_cancels_touch_but_not_held_wheel(self):
        for command in ('A','C'):
            self.assertEqual(self.run_steps([('T',0),('P',0),(command,1),('P',0),('D',0)]),
                             ['down','cancel'])
            self.assertEqual(self.run_steps([('T',0),('W',0),('P',0),(command,1),('P',0),
                                             ('D',0),('W',1),('P',0),('D',0)]), ['down','up'])

    def test_blocked_touch_cannot_start_then_reappear_when_unblocked(self):
        self.assertEqual(self.run_steps([('A',1),('T',0),('P',0),('A',0),('P',0),('D',0)]), [])

    def test_real_release_repress_in_same_poll_forms_two_sessions(self):
        self.assertEqual(self.run_steps([('W',0),('P',0),('D',0),('W',1),('T',0),('P',0),('D',0)]),
                         ['down','up','down'])

    def test_waking_wheel_preserves_wake_flag(self):
        self.assertEqual(self.run_steps([('H',0),('W',1),('P',0),('D',0)]), ['down wake','up'])

    def test_release_reader_preserves_following_down_through_tail(self):
        for end in (1, 2):
            self.assertEqual(self.run_steps([('I',end),('I',0),('G',1),('G',1),('G',0)]),
                             ['got1=1','got1=0','got0=1'])

    def test_other_board_duplicate_down_does_not_block_release(self):
        self.assertEqual(self.run_steps([('I',0),('I',1),('G',1)], board="legacy"), ['got1=1'])
        self.assertEqual(self.run_steps([('I',0),('I',0),('I',1),('G',1)], board="legacy"), ['got1=1'])

    def test_reply_interrupt_keeps_following_cancel(self):
        self.assertEqual(self.run_steps([('I',0),('I',2),('G',0),('G',1)]), ['got0=1','got1=1'])


if __name__ == '__main__':
    unittest.main()
