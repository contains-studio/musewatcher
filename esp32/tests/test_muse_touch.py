#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
# Licensed under the Apache License, Version 2.0.

"""Timed gesture, mixed-source PTT, and actual voice cancellation regressions.

The UI integration uses real LVGL in the simulator; these tests cover the
state boundaries and the voice task's cancellation cleanup with host fakes.
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

GESTURE = r'''
#include <stdio.h>
#include "muse_touch_gesture.h"
int main(void) {
    muse_touch_gesture_t g = {0};
    unsigned event, now;
    int x, y, enabled;
    while (scanf("%u %u %d %d %d", &now, &event, &x, &y, &enabled) == 5) {
        unsigned a = muse_touch_step(&g, event, now, x, y, enabled);
        if (a & MUSE_TOUCH_RECORD) puts("record");
        if (a & MUSE_TOUCH_SEND) puts("send");
        if (a & MUSE_TOUCH_CANCEL) puts("cancel");
        if (a & MUSE_TOUCH_PET) puts("pet");
        if (a & MUSE_TOUCH_CAMERA) puts("camera");
    }
    return 0;
}
'''

SOURCES = r'''
#include <stdio.h>
#include "muse_ptt_sources.h"
int main(void) {
    muse_ptt_sources_t s = {0};
    unsigned source;
    int down, cancel, wake, available;
    while (scanf("%u %d %d %d %d", &source, &down, &cancel, &wake, &available) == 5) {
        muse_ptt_source_set(&s, source, down, cancel, wake);
        for (int i = 0; available && i < 2; i++) {
            unsigned a = muse_ptt_source_next(&s);
            if (!a) break;
            printf("%s%s\n", a == MUSE_PTT_ACTION_DOWN ? "down"
                : a == MUSE_PTT_ACTION_UP ? "up" : "cancel",
                a == MUSE_PTT_ACTION_DOWN && s.wake ? " wake" : "");
            muse_ptt_source_commit(&s, a);
        }
    }
    return 0;
}
'''

VOICE_FAKES = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#define CONFIG_MUSE_BOARD_SENSECAP_WATCHER 1
#define pdTRUE 1
#define ESP_OK 0
#define ESP_LOGI(tag, ...) ((void)(tag), (void)snprintf(NULL, 0, __VA_ARGS__))
#define ESP_LOGW(...) ((void)0)
#define MUSE_AUDIO_CHUNK 4
#define MUSE_AUDIO_RATE 16000
#define MAX_FRAMES 80
#define TAIL_FRAMES 8
#define MIN_HELD_FRAMES 4
#define SETTLE_CHUNKS 1
#define MUSE_MODE_LISTENING 1
#define TAG "test"
typedef enum { MUSE_PTT_DOWN, MUSE_PTT_UP, MUSE_PTT_CANCEL } muse_ptt_t;
typedef struct { muse_ptt_t type; bool wake; } muse_input_event_t;
typedef enum { MUSE_HATCH_EV_NONE, MUSE_HATCH_EV_HEARD, MUSE_HATCH_EV_ERROR } muse_hatch_ev_t;
typedef struct { double acc; size_t frames; int peak, clipped; float floor_db;
    float tail_db[25]; size_t chunks; } rec_stats_t;
static void *s_queue;
static bool s_record_cancelled, s_live, s_tried;
static int16_t *s_rec, s_chunk[MUSE_AUDIO_CHUNK];
static size_t s_rec_n, s_sent, s_held_count, s_pre_fill;
static uint32_t s_rec_mic_generation;
static int reads, event_at, event_type, go_lives, cancels, resets, mute_at = -1;
static bool event_sent, pending_press, cancel_in_tail;
static int xQueuePeek(void *q, muse_input_event_t *e, int wait) {
    (void)q; (void)wait;
    if (pending_press) { e->type = MUSE_PTT_DOWN; return pdTRUE; }
    if (!event_sent && reads >= event_at) {
        e->type = event_type; e->wake = false; return pdTRUE;
    }
    return 0;
}
static int xQueueReceive(void *q, muse_input_event_t *e, int wait) {
    if (!xQueuePeek(q, e, wait)) return 0;
    if (pending_press) pending_press = false;
    else event_sent = true;
    return pdTRUE;
}
static void muse_state_poke(void) {}
static bool mic_current(uint32_t generation) {
    (void)generation; return mute_at < 0 || reads < mute_at;
}
static void muse_state_set_mode(int mode) { (void)mode; }
static void muse_state_set_progress(float p) { (void)p; }
static void muse_state_set_level(float p) { (void)p; }
static void muse_state_set_caption(const char *fmt, ...) { (void)fmt; }
static bool muse_hatch_ready(void) { return true; }
static int muse_settings_mic_gain(void) { return 0; }
static void go_live(void) { s_live = true; go_lives++; }
static int muse_audio_read(int16_t *pcm, size_t n) {
    (void)pcm; (void)n; reads++;
    if (cancel_in_tail && reads == 3) { event_type = MUSE_PTT_CANCEL; event_sent = false; }
    return ESP_OK;
}
static float muse_audio_level(int16_t *pcm, size_t n) { (void)pcm; (void)n; return 0; }
static void pre_get(size_t i, int16_t *pcm) { (void)i; (void)pcm; }
static void pre_reset(void) { resets++; }
static void take(rec_stats_t *stats, int16_t *pcm) {
    (void)pcm; stats->frames += MUSE_AUDIO_CHUNK; stats->chunks++;
    s_rec_n += MUSE_AUDIO_CHUNK;
}
static muse_hatch_ev_t muse_hatch_turn_event(char *out, size_t cap) {
    (void)out; (void)cap; return MUSE_HATCH_EV_NONE;
}
static void muse_hatch_turn_cancel(void) { cancels++; }
#define strlcpy test_strlcpy
static size_t test_strlcpy(char *out, const char *in, size_t cap) {
    snprintf(out, cap, "%s", in); return strlen(in);
}
'''

VOICE_MAIN = r'''
int main(int argc, char **argv) {
    assert(argc == 2);
    s_rec = malloc(MAX_FRAMES * sizeof(*s_rec));
    event_at = !strcmp(argv[1], "early") || !strcmp(argv[1], "interrupt") ? 0 : 2;
    event_type = !strcmp(argv[1], "release") ? MUSE_PTT_UP : MUSE_PTT_CANCEL;
    if (!strcmp(argv[1], "tail")) { event_type = MUSE_PTT_UP; cancel_in_tail = true; }
    if (!strcmp(argv[1], "mute")) { event_at = 100; mute_at = 2; }
    if (!strcmp(argv[1], "interrupt")) { pending_press = true; assert(got_event(MUSE_PTT_DOWN)); }
    size_t held = 0;
    char why[96] = {0};
    bool ok = record(false, &held, why, sizeof(why));
    if (!strcmp(argv[1], "release")) {
        assert(ok && s_rec != NULL && cancels == 0 && reads == 4);
        free(s_rec);
    } else {
        assert(!ok && s_rec == NULL && cancels == 1 && resets == 1);
        assert(held >= MIN_HELD_FRAMES);
        assert(!strcmp(why, !strcmp(argv[1], "mute") ? "MIC OFF" : "CANCELLED"));
        if (!strcmp(argv[1], "early") || !strcmp(argv[1], "interrupt")) assert(go_lives == 0 && reads == 0);
        else assert(go_lives == 1 && reads == (cancel_in_tail ? 3 : 2));
    }
    puts("ok");
    return 0;
}
'''


def function(source, name):
    match = re.search(r"^static (?:bool|void) " + name + r"\([^;]*?\)\n\{", source, re.M)
    if not match:
        raise AssertionError(f"missing function {name}")
    start = source.index("{", match.start())
    depth, end = 1, start + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[match.start():end]


class TouchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cc = shlex.split(os.environ.get("CC", "cc"))
        if not cc or not shutil.which(cc[0]):
            raise unittest.SkipTest("C compiler unavailable")
        cls.temp = tempfile.TemporaryDirectory()
        cls.binaries = {}
        voice = (MUSE / "muse_voice.c").read_text()
        programs = {"gesture": GESTURE, "sources": SOURCES,
                    "voice": VOICE_FAKES + function(voice, "got_event")
                    + function(voice, "record") + VOICE_MAIN}
        for name, code in programs.items():
            source = Path(cls.temp.name) / (name + ".c")
            source.write_text(code)
            binary = Path(cls.temp.name) / name
            result = subprocess.run([*cc, "-std=c11", "-Wall", "-Wextra", "-Werror",
                "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
                "-I", str(MUSE), str(source), "-lm", "-o", str(binary)],
                capture_output=True, text=True)
            if result.returncode:
                raise AssertionError(result.stdout + result.stderr)
            cls.binaries[name] = binary

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def run_program(self, name, rows=(), args=()):
        result = subprocess.run([str(self.binaries[name]), *args],
            input="".join(" ".join(map(str, row)) + "\n" for row in rows),
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.splitlines()

    def gestures(self, steps):
        return self.run_program("gesture", [(*row, *([100, 100, 1][len(row)-2:])) for row in steps])

    def test_hold_records_once_then_release_sends(self):
        self.assertEqual(self.gestures([(10, 1), (409, 0), (410, 0), (900, 0),
                                       (1000, 4), (1400, 0)]), ["record", "send"])

    def test_two_completed_taps_open_camera_without_record_or_pet(self):
        self.assertEqual(self.gestures([(10, 1), (100, 4), (300, 1), (340, 4),
                                       (900, 0)]), ["camera"])

    def test_second_tap_hold_records_instead_of_opening_camera(self):
        self.assertEqual(self.gestures([(10, 1), (100, 4), (300, 1), (700, 0),
                                       (1000, 4), (1600, 0)]), ["record", "send"])

    def test_single_tap_pets_only_after_double_tap_window(self):
        self.assertEqual(self.gestures([(10, 1), (50, 4), (400, 0)]), [])
        self.assertEqual(self.gestures([(10, 1), (50, 4), (401, 0), (900, 0)]), ["pet"])

    def test_drag_before_hold_cancels_pending_touch(self):
        self.assertEqual(self.gestures([(10, 1), (100, 2, 80, 100), (500, 0),
                                       (800, 4), (1200, 0)]), [])

    def test_drag_after_hold_discards_recording_and_does_not_arm_camera(self):
        self.assertEqual(self.gestures([(10, 1), (410, 0), (500, 2, 70, 100),
            (600, 4), (650, 1), (700, 4), (1100, 0)]), ["record", "cancel", "pet"])

    def test_small_finger_jitter_does_not_cancel_hold(self):
        self.assertEqual(self.gestures([(10, 1), (100, 2, 110, 108), (410, 0),
                                       (600, 4)]), ["record", "send"])

    def test_press_lost_cancels_and_release_cannot_send(self):
        self.assertEqual(self.gestures([(10, 1), (410, 0), (500, 8), (700, 4),
                                       (900, 0)]), ["record", "cancel"])

    def test_blocked_wake_touch_stays_wake_only(self):
        self.assertEqual(self.gestures([(10, 1, 100, 100, 0), (500, 0), (600, 4),
                                       (1100, 0)]), [])

    def test_screen_change_cancels_hold_and_pending_single_tap(self):
        self.assertEqual(self.gestures([(10, 1), (410, 0), (500, 0, 100, 100, 0),
                                       (700, 4)]), ["record", "cancel"])
        self.assertEqual(self.gestures([(10, 1), (50, 4), (100, 0, 100, 100, 0),
                                       (700, 0)]), [])

    def test_hold_release_does_not_seed_double_tap(self):
        self.assertEqual(self.gestures([(10, 1), (410, 0), (500, 4), (550, 1),
                                       (600, 4), (1000, 0)]), ["record", "send", "pet"])

    def test_millisecond_wrap_preserves_hold(self):
        start = 2**32 - 200
        self.assertEqual(self.gestures([(start, 1), (200, 0), (800, 4)]), ["record", "send"])

    def test_overlapping_wheel_touch_releases_only_after_last_source(self):
        for first, second in ((1, 2), (2, 1)):
            self.assertEqual(self.run_program("sources", [(first, 1, 0, 0, 1),
                (second, 1, 0, 0, 1), (first, 0, 0, 0, 1), (second, 0, 0, 0, 1)]), ["down", "up"])

    def test_lost_touch_cannot_cancel_held_wheel(self):
        self.assertEqual(self.run_program("sources", [(2, 1, 0, 0, 1), (1, 1, 0, 0, 1),
            (2, 0, 1, 0, 1), (1, 0, 0, 0, 1)]), ["down", "up"])

    def test_cancel_when_touch_is_last_source(self):
        self.assertEqual(self.run_program("sources", [(1, 1, 0, 0, 1), (2, 1, 0, 0, 1),
            (1, 0, 0, 0, 1), (2, 0, 1, 0, 1)]), ["down", "cancel"])

    def test_full_queue_keeps_release_until_retry_and_orders_next_press(self):
        self.assertEqual(self.run_program("sources", [(2, 1, 0, 0, 1), (2, 0, 0, 0, 0),
            (1, 1, 0, 0, 0), (1, 1, 0, 0, 1), (1, 0, 0, 0, 1)]), ["down", "up", "down", "up"])

    def test_physical_wake_flag_preserved(self):
        self.assertEqual(self.run_program("sources", [(1, 1, 0, 1, 1), (1, 0, 0, 0, 1)]),
                         ["down wake", "up"])

    def test_voice_cancel_before_record_discards_without_starting_upload(self):
        self.assertEqual(self.run_program("voice", args=["early"]), ["ok"])

    def test_interrupting_reply_preserves_already_queued_cancellation(self):
        self.assertEqual(self.run_program("voice", args=["interrupt"]), ["ok"])

    def test_voice_cancel_during_record_discards_partial_note(self):
        self.assertEqual(self.run_program("voice", args=["during"]), ["ok"])

    def test_voice_release_preserves_tail_and_retained_note(self):
        self.assertEqual(self.run_program("voice", args=["release"]), ["ok"])

    def test_cancel_during_release_tail_still_discards_note(self):
        self.assertEqual(self.run_program("voice", args=["tail"]), ["ok"])

    def test_voice_mute_still_discards_partial_note(self):
        self.assertEqual(self.run_program("voice", args=["mute"]), ["ok"])


if __name__ == "__main__":
    unittest.main()
