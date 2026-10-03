#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
# Licensed under the Apache License, Version 2.0.

"""Exercise the real JPEG serializer and voice photo API/reply loop with host fakes.

The session itself still needs a live protocol check; these tests do not claim
that a Muse server accepts the candidate image-item format.
"""
import base64
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
MUSE = ROOT / "components" / "muse"


def function(source, name):
    match = re.search(r"^(?:static )?(?:bool|void|size_t|uint32_t|esp_err_t|feed_t) " + name + r"\([^;]*?\)\n\{", source, re.M)
    if not match:
        raise AssertionError(f"missing function {name}")
    start = source.index("{", match.start())
    depth = 1
    end = start + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[match.start():end]


FAKES = r'''
#include <assert.h>
#include <stdarg.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "muse_photo_upload.h"
#define CONFIG_MUSE_HATCH 1
#define MALLOC_CAP_SPIRAM 1
#define MALLOC_CAP_8BIT 2
#define pdTRUE 1
#define pdMS_TO_TICKS(ms) (ms)
#define MUSE_AUDIO_CHUNK 4
#define MUSE_AUDIO_RATE 16000
#define MUSE_CAPTION_MAX 256
#define ACK_WAIT_US 30000000LL
#define ESP_LOGI(...) ((void)0)
#define ESP_LOGW(...) ((void)0)
#define TAG "test"
enum { MUSE_MODE_IDLE, MUSE_MODE_THINKING, MUSE_MODE_SPEAKING, MUSE_PTT_DOWN };
typedef enum { MUSE_HATCH_EV_NONE, MUSE_HATCH_EV_HEARD, MUSE_HATCH_EV_REPLY,
    MUSE_HATCH_EV_DONE, MUSE_HATCH_EV_ERROR, MUSE_HATCH_EV_SENT } muse_hatch_ev_t;
typedef void (*muse_photo_sent_cb_t)(bool, const char *, void *);
typedef struct { uint8_t *jpeg; size_t len; muse_photo_sent_cb_t callback; void *ctx; } photo_request_t;
static void *s_photos = (void *)1;
static atomic_bool s_photo_busy;
static photo_request_t pending;
static bool ready = true, alloc_fail, queue_fail, queue_full, cancel;
static int mode, allocations, releases, callbacks, cancellations;
static bool callback_sent;
static char callback_error[96];
static muse_hatch_ev_t events[8];
static size_t event_at;
static int64_t clock_us, clock_step;
static bool muse_hatch_ready(void) { return ready; }
static int muse_state_mode(void *p) { (void)p; return mode; }
static void *heap_caps_malloc(size_t n, int caps) {
    (void)caps; if (alloc_fail) return NULL; allocations++; return malloc(n);
}
static void tracked_free(void *p) { if (p) releases++; free(p); }
static int xQueueSend(void *q, const void *p, int wait) {
    (void)q; (void)wait;
    if (queue_fail || queue_full) return 0;
    pending = *(const photo_request_t *)p; queue_full = true; return pdTRUE;
}
static void muse_state_set_asleep(bool value) { (void)value; }
static void muse_state_poke(void) {}
static void muse_state_nudge(void) {}
static void muse_state_set_mode(int value) { mode = value; }
static void muse_state_set_caption(const char *fmt, ...) { (void)fmt; }
static void muse_state_set_level(float value) { (void)value; }
static int64_t esp_timer_get_time(void) { clock_us += clock_step; return clock_us; }
static muse_hatch_ev_t muse_hatch_turn_event(char *text, size_t cap) {
    muse_hatch_ev_t ev = events[event_at];
    if (ev) event_at++;
    snprintf(text, cap, "%s", ev == MUSE_HATCH_EV_ERROR ? "TEST FAILURE" : "reply");
    return ev;
}
static bool muse_hatch_turn_caption(size_t played, char *text, size_t cap) {
    (void)played; (void)text; (void)cap; return false;
}
static bool got_event(int type) { (void)type; return cancel; }
static void muse_hatch_turn_cancel(void) { cancellations++; }
static size_t muse_hatch_turn_read(int16_t *pcm, size_t n, int wait) {
    (void)pcm; (void)n; (void)wait; return 0;
}
static float muse_audio_level(const int16_t *pcm, size_t n) { (void)pcm; (void)n; return 0; }
static void muse_audio_write(const int16_t *pcm, size_t n) { (void)pcm; (void)n; }
static void vTaskDelay(int ms) { (void)ms; }
static void go_idle(const char *caption) { (void)caption; mode = MUSE_MODE_IDLE; }
static void result(bool sent, const char *error, void *ctx) {
    assert(ctx == (void *)42); callbacks++; callback_sent = sent;
    snprintf(callback_error, sizeof(callback_error), "%s", error ? error : "");
}
#define free tracked_free
'''

MAIN = r'''
#undef free
static size_t writes, max_write;
static int fail_at;
static bool closed;
static bool output(const uint8_t *data, size_t len, bool last, void *ctx) {
    (void)ctx; writes++;
    if (len > max_write) max_write = len;
    if (fail_at && writes == (size_t)fail_at) return false;
    fwrite(data, 1, len, stdout); closed = last; return true;
}
static void reset_reply(void) {
    callbacks = cancellations = 0; callback_sent = false; callback_error[0] = 0;
    event_at = 0; memset(events, 0, sizeof(events)); cancel = false;
    clock_us = 0; clock_step = 1000000;
}
static void lifecycle(void) {
    uint8_t jpeg[] = {255, 216, 7, 255, 217};
    assert(!muse_voice_send_photo(NULL, 5, result, (void *)42));
    ready = false;
    assert(!muse_voice_send_photo(jpeg, 5, result, (void *)42));
    ready = true; mode = MUSE_MODE_THINKING;
    assert(!muse_voice_send_photo(jpeg, 5, result, (void *)42));
    mode = MUSE_MODE_IDLE; alloc_fail = true;
    assert(!muse_voice_send_photo(jpeg, 5, result, (void *)42));
    assert(!atomic_load(&s_photo_busy));
    alloc_fail = false; queue_fail = true;
    assert(!muse_voice_send_photo(jpeg, 5, result, (void *)42));
    assert(allocations == releases && callbacks == 0 && !atomic_load(&s_photo_busy));
    queue_fail = false;
    assert(muse_voice_send_photo(jpeg, 5, result, (void *)42));
    assert(pending.jpeg != jpeg && pending.len == 5 && pending.jpeg[2] == 7);
    jpeg[2] = 9;
    assert(pending.jpeg[2] == 7);
    assert(!muse_voice_send_photo(jpeg, 5, result, (void *)42));
    assert(callbacks == 0);  /* queued bytes are not a delivery acknowledgement */
    photo_complete(&pending, false, "CANCELLED");
    photo_complete(&pending, true, NULL);
    assert(callbacks == 1 && !callback_sent && !strcmp(callback_error, "CANCELLED"));
    tracked_free(pending.jpeg); pending.jpeg = NULL;
    assert(allocations == releases);

    photo_request_t photo = {NULL, 0, result, (void *)42};
    bool delivered;
    reset_reply(); events[0] = MUSE_HATCH_EV_SENT; events[1] = MUSE_HATCH_EV_ERROR;
    assert(!hatch_reply(&delivered, &photo));
    assert(callbacks == 1 && callback_sent && delivered);

    reset_reply(); photo.callback = result; events[0] = MUSE_HATCH_EV_ERROR;
    assert(!hatch_reply(&delivered, &photo));
    assert(callbacks == 1 && !callback_sent && !strcmp(callback_error, "TEST FAILURE"));

    reset_reply(); photo.callback = result; cancel = true;
    assert(hatch_reply(&delivered, &photo));
    assert(callbacks == 1 && !callback_sent && cancellations == 1);

    reset_reply(); photo.callback = result; events[0] = MUSE_HATCH_EV_REPLY; events[1] = MUSE_HATCH_EV_DONE;
    assert(!hatch_reply(&delivered, &photo));
    assert(callbacks == 1 && callback_sent);

    reset_reply(); photo.callback = result; events[0] = MUSE_HATCH_EV_DONE;
    assert(!hatch_reply(&delivered, &photo));
    assert(callbacks == 1 && !callback_sent);  /* DONE alone does not establish upload acceptance */

    reset_reply(); photo.callback = result; clock_step = 10000000;
    assert(!hatch_reply(&delivered, &photo));
    assert(callbacks == 1 && !callback_sent && cancellations == 1);
    assert(!strcmp(callback_error, "PHOTO SEND NOT CONFIRMED"));
    puts("ok");
}
int main(int argc, char **argv) {
    if (argc > 1 && !strcmp(argv[1], "lifecycle")) { lifecycle(); return 0; }
    if (argc > 1) fail_at = atoi(argv[1]);
    uint8_t *jpeg = malloc(MUSE_PHOTO_MAX_BYTES + 1);
    size_t n = fread(jpeg, 1, MUSE_PHOTO_MAX_BYTES + 1, stdin);
    char scratch[8192];
    bool ok = muse_photo_write_json(jpeg, n, scratch, sizeof(scratch), muse_hatch_base64, output, NULL);
    fprintf(stderr, "{\"ok\":%s,\"closed\":%s,\"writes\":%zu,\"max_write\":%zu}\n",
        ok ? "true" : "false", closed ? "true" : "false", writes, max_write);
    free(jpeg); return 0;
}
'''


class PhotoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cc = shlex.split(os.environ.get("CC", "cc"))
        if not cc or not shutil.which(cc[0]):
            raise unittest.SkipTest("C compiler unavailable")
        voice = (MUSE / "muse_voice.c").read_text()
        text = (MUSE / "muse_chat_text.c").read_text()
        cls.tmp = tempfile.TemporaryDirectory()
        directory = Path(cls.tmp.name)
        source = directory / "photo.c"
        source.write_text(FAKES + "\n" + function(text, "muse_hatch_base64") + "\n"
            + function(voice, "photo_complete") + "\n" + function(voice, "hatch_reply") + "\n"
            + function(voice, "muse_voice_send_photo") + "\n" + MAIN)
        cls.binary = directory / "photo"
        result = subprocess.run([*cc, "-std=c11", "-Wall", "-Wextra", "-Werror",
            "-fsanitize=address,undefined", "-I", str(MUSE), str(source), "-o", str(cls.binary)],
            capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_upload(self, data, fail_at=0):
        result = subprocess.run([str(self.binary), str(fail_at)], input=data, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        return result.stdout, json.loads(result.stderr)

    def test_binary_survives_chunk_and_base64_boundaries(self):
        for length in (4, 5, 6, 6143, 6144, 6145, 192 * 1024):
            with self.subTest(length=length):
                jpeg = b"\xff\xd8" + bytes(i % 256 for i in range(length - 4)) + b"\xff\xd9"
                wire, metrics = self.run_upload(jpeg)
                self.assertTrue(metrics["ok"] and metrics["closed"])
                self.assertLessEqual(metrics["max_write"], 8192)
                body = json.loads(wire)
                self.assertEqual(body["output_modality"], "text")
                item, = body["items"]
                self.assertEqual((item["type"], item["mime_type"], item["filename"]),
                    ("image", "image/jpeg", "watcher.jpg"))
                self.assertEqual(base64.b64decode(item["data_base64"], validate=True), jpeg)

    def test_bad_or_oversize_input_sends_nothing(self):
        for data in (b"", b"bad", b"\xff\xd8\x00\x00", b"\xff\xd8" + b"x" * (192 * 1024) + b"\xff\xd9"):
            wire, metrics = self.run_upload(data)
            self.assertEqual(wire, b"")
            self.assertFalse(metrics["ok"])
            self.assertEqual(metrics["writes"], 0)

    def test_failed_or_cancelled_write_never_closes_or_retries(self):
        jpeg = b"\xff\xd8" + b"x" * 20000 + b"\xff\xd9"
        for fail_at in (1, 2, 4, 6):
            _, metrics = self.run_upload(jpeg, fail_at)
            self.assertFalse(metrics["ok"] or metrics["closed"])
            self.assertEqual(metrics["writes"], fail_at)

    def test_copy_ownership_busy_rejection_and_exactly_once_results(self):
        result = subprocess.run([str(self.binary), "lifecycle"], capture_output=True)
        self.assertEqual((result.returncode, result.stdout), (0, b"ok\n"), result.stderr.decode())


MIC_FAKES = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#define HOLD_NOTES 1
#define MUSE_LOW_MEM 0
#define MUSE_AUDIO_CHUNK 4
#define MUSE_AUDIO_RATE 40
#define CHANNELS 2
#define PRE_CHUNKS 4
#define SETTLE_CHUNKS 2
#define STALL_US 30000000
#define pdMS_TO_TICKS(x) (x)
#define ESP_LOGW(...) ((void)0)
#define TAG "test"
#define ESP_OK 0
#define ESP_FAIL 1
#define ESP_ERR_INVALID_STATE 2
#define ESP_CODEC_DEV_OK 0
#define MUSE_MODE_THINKING 1
typedef int esp_err_t;
typedef enum { FED, FEED_FAILED, FEED_PRESSED, FEED_MUTED } feed_t;
typedef enum { MUSE_HATCH_EV_NONE, MUSE_HATCH_EV_ERROR } muse_hatch_ev_t;
typedef struct { int16_t pcm[MUSE_AUDIO_CHUNK]; } pre_chunk_t;
static pre_chunk_t pre[PRE_CHUNKS], *s_pre = pre;
static size_t s_pre_fill, s_pre_next;
static uint32_t generation, s_pre_mic_generation, s_rec_mic_generation, s_read_generation;
static bool mic_on = true, s_read_generation_valid, s_monitor = true;
static float s_monitor_db, s_hpf_x1, s_hpf_y1, s_hpf_a = 0.5f;
static int s_settle;
static int16_t s_chunk[MUSE_AUDIO_CHUNK], s_in_stereo[MUSE_AUDIO_CHUNK * CHANNELS];
static void *s_mic;
static struct { int mic_slot; } board = {0}, *muse_board = &board;
static int16_t *s_rec;
static size_t s_rec_n, s_sent;
static bool s_live, s_tried;
static int reads, read_toggle_at, read_error_at, toggle_mode, writes, write_toggle_at;
static int ends, cancels, held_notes, drops, chirp_toggle;
static uint32_t muse_settings_mic_generation(void) { return generation; }
static bool muse_settings_mic_on(void) { return mic_on; }
static void toggle(void) { generation += toggle_mode == 2 ? 2 : 1; mic_on = toggle_mode == 2; }
static int esp_codec_dev_read(void *codec, void *out, size_t bytes) {
    (void)codec; reads++;
    int16_t *pcm = out;
    for (size_t i = 0; i < bytes / sizeof(*pcm); i++) pcm[i] = (int16_t)(reads * 100);
    if (reads == read_toggle_at) toggle();
    return reads == read_error_at ? 1 : ESP_CODEC_DEV_OK;
}
static void vTaskDelay(int ms) { (void)ms; }
static float muse_audio_dbfs(const int16_t *pcm, size_t n) { (void)pcm; (void)n; return -20; }
static int64_t esp_timer_get_time(void) { static int64_t t; return t += 1000; }
static muse_hatch_ev_t muse_hatch_turn_event(char *text, size_t cap) { (void)text; (void)cap; return MUSE_HATCH_EV_NONE; }
static bool press_waiting(void) { return false; }
static size_t muse_hatch_turn_audio_wait(const int16_t *pcm, size_t n, int ms) {
    (void)pcm; (void)ms; writes++; if (writes == write_toggle_at) toggle(); return n > 2 ? 2 : n;
}
static void muse_hatch_turn_end(void) { ends++; }
static void muse_hatch_turn_cancel(void) { cancels++; }
static void muse_audio_chirp(int n) { (void)n; if (chirp_toggle) toggle(); }
static void muse_state_set_mode(int m) { (void)m; }
static void muse_state_set_caption(const char *s) { (void)s; }
static bool hatch_reply(bool *delivered, void *photo) { (void)photo; *delivered = true; return false; }
static void drop_rec(void) { free(s_rec); s_rec = NULL; drops++; }
static void hold_rec(bool tried) { (void)tried; held_notes++; free(s_rec); s_rec = NULL; }
static void go_idle(const char *s) { (void)s; }
'''

MIC_MAIN = r'''
static void reset(void) {
    mic_on = true; generation = s_pre_mic_generation = s_rec_mic_generation = 0;
    reads = writes = ends = cancels = held_notes = drops = 0;
    read_toggle_at = read_error_at = write_toggle_at = chirp_toggle = 0;
    s_read_generation = 0; s_read_generation_valid = false; s_settle = 0;
    s_pre_fill = 3; s_pre_next = 0; s_monitor_db = -10; s_hpf_x1 = 200; s_hpf_y1 = 100;
}
static void zeroed(const int16_t *pcm, size_t n) { for (size_t i = 0; i < n; i++) assert(pcm[i] == 0); }
int main(int argc, char **argv) {
    assert(argc == 2);
    reset();
    if (!strcmp(argv[1], "audio")) {
        int16_t out[8];
        mic_on = false; memset(out, 1, sizeof(out));
        assert(muse_audio_read(out, 8) == ESP_ERR_INVALID_STATE && reads == 0); zeroed(out, 8);
        for (int fast = 1; fast <= 2; fast++) {
            reset(); toggle_mode = fast; read_toggle_at = 4; memset(out, 1, sizeof(out));
            assert(muse_audio_read(out, 8) == ESP_ERR_INVALID_STATE); zeroed(out, 8);
            assert(!s_read_generation_valid && s_hpf_x1 == 0 && s_hpf_y1 == 0);
        }
        reset(); assert(muse_audio_read(out, 4) == ESP_OK && reads == 4); /* 3 drain + 1 sample */
        assert(muse_audio_read(out, 4) == ESP_OK && reads == 5);
        toggle_mode = 2; toggle();
        assert(muse_audio_read(out, 4) == ESP_OK && reads == 9); /* fresh drain after quick OFF/ON */
        read_error_at = 11; memset(out, 1, sizeof(out));
        assert(muse_audio_read(out, 8) == ESP_FAIL); zeroed(out, 8); /* wipe earlier successful chunk too */
    } else if (!strcmp(argv[1], "preroll")) {
        mic_on = false; idle_capture(); assert(!reads && !s_pre_fill && s_monitor_db == -100);
        reset(); toggle_mode = 2; toggle(); idle_capture();
        assert(!s_pre_fill && s_settle == SETTLE_CHUNKS - 1); /* old ring erased despite mic now ON */
        idle_capture(); assert(!s_pre_fill); idle_capture(); assert(s_pre_fill == 1);
        reset(); toggle_mode = 2; read_toggle_at = 4; idle_capture();
        assert(!s_pre_fill && s_monitor_db == -100); /* toggle during blocking read */
    } else if (!strcmp(argv[1], "upload")) {
        int16_t pcm[8] = {0}; size_t sent = 0;
        mic_on = false; assert(feed_rest(pcm, 8, &sent, false, 0) == FEED_MUTED && !writes && !ends);
        for (int fast = 1; fast <= 2; fast++) {
            reset(); sent = 0; toggle_mode = fast; write_toggle_at = 1;
            assert(feed_rest(pcm, 8, &sent, false, 0) == FEED_MUTED && writes == 1 && !ends);
        }
        reset(); sent = 0; assert(feed_rest(pcm, 8, &sent, false, 0) == FED && sent == 8 && ends == 1);
    } else if (!strcmp(argv[1], "partial")) {
        for (int stage = 0; stage < 3; stage++) {
            reset(); s_rec = calloc(8, sizeof(*s_rec)); s_rec_n = 8; s_sent = 0; s_live = true;
            toggle_mode = 2;
            if (stage == 0) toggle();
            if (stage == 1) chirp_toggle = 1;
            if (stage == 2) write_toggle_at = 1;
            assert(!finish_note() && !s_rec && !held_notes && drops == 1 && cancels >= 1 && !ends);
        }
        reset(); s_rec = calloc(8, sizeof(*s_rec)); s_rec_n = 8; s_sent = 0; s_live = false;
        assert(!finish_note() && held_notes == 1 && drops == 0); /* offline notes still save normally */
    } else assert(false);
    return 0;
}
'''


class MicPrivacyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cc = shlex.split(os.environ.get("CC", "cc"))
        if not cc or not shutil.which(cc[0]):
            raise unittest.SkipTest("C compiler unavailable")
        voice = (MUSE / "muse_voice.c").read_text()
        audio = (MUSE / "muse_audio.c").read_text()
        cls.tmp = tempfile.TemporaryDirectory()
        source = Path(cls.tmp.name) / "mic.c"
        source.write_text(MIC_FAKES + "\n" + function(audio, "muse_audio_read") + "\n"
            + "\n".join(function(voice, name) for name in (
                "pre_reset", "mic_current", "pre_sync_mic", "idle_capture", "feed_rest", "finish_note"))
            + "\n" + MIC_MAIN)
        cls.binary = Path(cls.tmp.name) / "mic"
        result = subprocess.run([*cc, "-std=c11", "-Wall", "-Wextra", "-Werror",
            "-fsanitize=address,undefined", str(source), "-o", str(cls.binary)], capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_case(self, case):
        result = subprocess.run([str(self.binary), case], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_mute_cancels_codec_read_and_unmute_drains_old_dma(self):
        self.run_case("audio")

    def test_quick_toggle_erases_preroll_and_monitor(self):
        self.run_case("preroll")

    def test_mute_or_quick_toggle_stops_queued_note_upload(self):
        self.run_case("upload")

    def test_muted_partial_is_discarded_instead_of_saved(self):
        self.run_case("partial")


if __name__ == "__main__":
    unittest.main()
