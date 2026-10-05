#!/usr/bin/env python3
# Copyright (c) contains-studio.
# Licensed under the Apache License, Version 2.0.

"""Run the production voice cancel/upload/reply paths against host transports.

No Muse service is contacted. The fake transport injects a wheel cancellation
while a real firmware loop is waiting for upload room or a reply.
"""
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
import unittest

from test_muse_photo import function

MUSE = Path(__file__).resolve().parents[1] / "components" / "muse"

FAKES = r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdarg.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define HOLD_NOTES 1
#define MUSE_AUDIO_CHUNK 4
#define MUSE_AUDIO_RATE 16000
#define MUSE_CAPTION_MAX 256
#define SETTLE_CHUNKS 2
#define ACK_WAIT_US 30000000LL
#define STALL_US 30000000LL
#define HELD_TRIES 8
#define RETRY_MIN_US 15000000LL
#define ESP_LOGI(...) ((void)0)
#define ESP_LOGW(...) ((void)0)
#define pdMS_TO_TICKS(x) (x)
enum { MUSE_MODE_IDLE, MUSE_MODE_LISTENING, MUSE_MODE_THINKING, MUSE_MODE_SPEAKING, MUSE_PTT_DOWN };
typedef enum { MUSE_HATCH_EV_NONE, MUSE_HATCH_EV_HEARD, MUSE_HATCH_EV_SENT,
    MUSE_HATCH_EV_REPLY, MUSE_HATCH_EV_DONE, MUSE_HATCH_EV_ERROR } muse_hatch_ev_t;
typedef void (*muse_photo_sent_cb_t)(bool, const char *, void *);
typedef struct { uint8_t *jpeg; size_t len; muse_photo_sent_cb_t callback; void *ctx; } photo_request_t;
typedef struct { int16_t *pcm; size_t frames; int64_t at_us; int tries; } held_note_t;
static int16_t *s_rec;
static size_t s_rec_n, s_sent, s_pre_fill;
static int s_settle;
static bool s_live, s_tried;
static uint32_t s_rec_mic_generation;
static held_note_t s_held[4];
static int s_held_count;
static int64_t s_next_send_us, s_send_backoff_us;
static int mode, cancels, begins, ends, writes, reads, ptt_polls, holds, callbacks, audio_writes;
static bool mic_on = true, cancel_on_write, cancel_on_read, delivered_event, sent_event;
static bool audio_arrives, cancel_on_delay, mute_on_write, cancel_on_error;
static bool error_pending, cancel_on_hold, cancel_on_backoff, cancel_on_callback, late_accepted;
static int mic_checks, cancel_at_mic;
static bool callback_sent, called_api;
static float level, progress;
static char caption[256], error[96];
static void go_idle(const char *text);
uint32_t muse_voice_thinking_turn(void);
bool muse_voice_cancel_thinking(uint32_t generation);
static int muse_state_mode(void *p) { (void)p; return mode; }
static void muse_state_set_mode(int value) { mode = value; }
static void muse_state_set_caption(const char *fmt, ...) {
    va_list ap; va_start(ap, fmt); vsnprintf(caption, sizeof(caption), fmt, ap); va_end(ap);
}
static void muse_state_set_level(float value) { level = value; }
static void muse_state_set_progress(float value) { progress = value; }
static void muse_state_nudge(void) {}
static bool muse_settings_mic_on(void) {
    if (++mic_checks == cancel_at_mic) {
        called_api = muse_voice_cancel_thinking(muse_voice_thinking_turn());
    }
    return mic_on;
}
static uint32_t muse_settings_mic_generation(void) { return 0; }
static void muse_hatch_turn_cancel(void) { cancels++; }
static void muse_hatch_turn_begin(void) { begins++; }
static void muse_hatch_turn_end(void) { ends++; }
static int64_t esp_timer_get_time(void) { static int64_t t; return t += 1000; }
static bool got_event(int type) { (void)type; ptt_polls++; return false; }
static bool press_waiting(void) { return false; }
static void request_cancel(void) {
    int before = cancels;
    assert(muse_voice_cancel_thinking(muse_voice_thinking_turn())); called_api = true;
    assert(cancels == before); /* the UI caller never closes transport streams */
}
static size_t muse_hatch_turn_audio_wait(const int16_t *pcm, size_t n, int ms) {
    (void)pcm; (void)ms; writes++;
    if (cancel_on_write) { cancel_on_write = false; request_cancel(); }
    if (mute_on_write) mic_on = false;
    return n > 2 ? 2 : n;
}
static muse_hatch_ev_t muse_hatch_turn_event(char *text, size_t cap) {
    snprintf(text, cap, "reply");
    if (error_pending) { error_pending = false; return MUSE_HATCH_EV_ERROR; }
    if (cancel_on_error) { cancel_on_error = false; request_cancel(); return MUSE_HATCH_EV_ERROR; }
    if (sent_event) { sent_event = false; return MUSE_HATCH_EV_SENT; }
    if (delivered_event) { delivered_event = false; return MUSE_HATCH_EV_DONE; }
    return MUSE_HATCH_EV_NONE;
}
static size_t muse_hatch_turn_read(int16_t *pcm, size_t n, int wait) {
    (void)pcm; (void)n; (void)wait; assert(++reads < 10);
    if (cancel_on_read) { cancel_on_read = false; request_cancel(); }
    return audio_arrives ? n : 0;
}
static bool muse_hatch_turn_caption(size_t n, char *text, size_t cap) { (void)n; (void)text; (void)cap; return false; }
static float muse_audio_level(const int16_t *p, size_t n) { (void)p; (void)n; return 0; }
static void muse_audio_write(const int16_t *p, size_t n) { (void)p; (void)n; audio_writes++; }
static void muse_audio_chirp(int n) { (void)n; }
static void vTaskDelay(int ms) {
    (void)ms; if (cancel_on_delay) { cancel_on_delay = false; request_cancel(); }
}
static void hold_rec(bool tried) {
    (void)tried; holds++;
    if (cancel_on_hold) late_accepted = muse_voice_cancel_thinking(muse_voice_thinking_turn());
    free(s_rec); s_rec = NULL;
}
static void back_off(void) {
    s_next_send_us = 15000000;
    if (cancel_on_backoff) late_accepted = muse_voice_cancel_thinking(muse_voice_thinking_turn());
}
static void result(bool sent, const char *text, void *ctx) {
    (void)ctx; callbacks++; callback_sent = sent; snprintf(error, sizeof(error), "%s", text ? text : "");
    if (cancel_on_callback) late_accepted = muse_voice_cancel_thinking(muse_voice_thinking_turn());
}
'''

MAIN = r'''
static void note(void) {
    s_rec = calloc(8, sizeof(*s_rec)); assert(s_rec); s_rec_n = 8;
    s_rec_mic_generation = 0; s_sent = 0; s_live = s_tried = true;
}
static void clean_idle(void) {
    assert(mode == MUSE_MODE_IDLE && !caption[0] && !level && !progress);
    assert(cancels == 1 && !audio_writes && !holds);
}
int main(int argc, char **argv) {
    assert(argc == 2);
    level = progress = 0.8f; snprintf(caption, sizeof(caption), "waiting");
    if (!strcmp(argv[1], "reject")) {
        int modes[] = {MUSE_MODE_IDLE, MUSE_MODE_LISTENING, MUSE_MODE_SPEAKING};
        for (size_t i = 0; i < sizeof(modes) / sizeof(*modes); i++) {
            mode = modes[i]; assert(!muse_voice_thinking_turn());
            assert(!muse_voice_cancel_thinking(0));
            assert(!muse_voice_cancel_thinking(4));
            assert(!cancel_thinking() && !cancels);
        }
    } else if (!strcmp(argv[1], "bench")) {
        mic_on = false; mode = MUSE_MODE_THINKING; request_cancel();
        assert(cancel_thinking()); clean_idle();
        assert(!cancel_thinking() && !ptt_polls);
    } else if (!strcmp(argv[1], "photo") || !strcmp(argv[1], "photo-acked") || !strcmp(argv[1], "audio")) {
        photo_request_t photo = {.callback = result}; bool delivered;
        mic_on = false; cancel_on_read = true; sent_event = !strcmp(argv[1], "photo-acked");
        bool acked = sent_event;
        audio_arrives = !strcmp(argv[1], "audio");
        assert(!hatch_reply(&delivered, &photo)); clean_idle();
        assert(called_api && callbacks == 1 && callback_sent == acked && delivered == acked);
        if (!acked) assert(!strcmp(error, "PHOTO SEND CANCELLED"));
    } else if (!strcmp(argv[1], "upload") || !strcmp(argv[1], "upload-muted")) {
        note(); cancel_on_write = true;
        mute_on_write = !strcmp(argv[1], "upload-muted");
        assert(!finish_note()); clean_idle();
        assert(!s_rec && writes == 1 && !ends && !reads && called_api);
    } else if (!strcmp(argv[1], "reply") || !strcmp(argv[1], "text")) {
        note(); s_sent = s_rec_n;
        if (!strcmp(argv[1], "text")) delivered_event = cancel_on_delay = true;
        else cancel_on_read = true;
        assert(!finish_note()); clean_idle();
        assert(!s_rec && ends == 1 && called_api);
    } else if (!strcmp(argv[1], "saved")) {
        s_held_count = 2;
        for (int i = 0; i < 2; i++) s_held[i] = (held_note_t){calloc(8, sizeof(int16_t)), 8, 0, 0};
        int16_t *other = s_held[1].pcm; cancel_on_write = true;
        assert(!send_held(false)); clean_idle();
        assert(s_held_count == 1 && s_held[0].pcm == other && !s_held[0].tries);
        assert(writes == 1 && !ends); drop_oldest();
    } else if (!strcmp(argv[1], "reply-error") || !strcmp(argv[1], "upload-error")) {
        note(); cancel_on_error = true;
        if (!strcmp(argv[1], "reply-error")) s_sent = s_rec_n;
        assert(!finish_note()); clean_idle();
        assert(called_api && !s_rec && !writes);
    } else if (!strcmp(argv[1], "saved-error")) {
        s_held_count = 2;
        for (int i = 0; i < 2; i++) s_held[i] = (held_note_t){calloc(8, sizeof(int16_t)), 8, 0, 0};
        int16_t *other = s_held[1].pcm; cancel_on_error = true;
        assert(!send_held(false)); clean_idle();
        assert(called_api && s_held_count == 1 && s_held[0].pcm == other && !s_held[0].tries);
        drop_oldest();
    } else if (!strcmp(argv[1], "boundary")) {
        note(); error_pending = true; cancel_at_mic = 3;
        assert(!finish_note()); clean_idle();
        assert(called_api && !s_rec && !holds);
    } else if (!strcmp(argv[1], "late")) {
        note(); error_pending = cancel_on_hold = true;
        assert(!finish_note());
        assert(holds == 1 && !late_accepted && !muse_voice_thinking_turn());
    } else if (!strcmp(argv[1], "saved-late")) {
        s_held_count = 1; s_held[0] = (held_note_t){calloc(8, sizeof(int16_t)), 8, 0, 0};
        error_pending = cancel_on_backoff = true;
        assert(!send_held(false));
        assert(s_held_count == 1 && s_held[0].tries == 1 && !late_accepted && !muse_voice_thinking_turn());
        drop_oldest();
    } else if (!strcmp(argv[1], "photo-final") || !strcmp(argv[1], "photo-ack-cancel")) {
        photo_request_t photo = {.callback = result}; bool delivered;
        cancel_on_callback = true;
        bool acked = !strcmp(argv[1], "photo-ack-cancel");
        if (acked) sent_event = true;
        else error_pending = true;
        assert(!hatch_reply(&delivered, &photo));
        assert(callbacks == 1 && callback_sent == acked && late_accepted == acked);
        assert(!muse_voice_thinking_turn());
        if (acked) clean_idle();
    } else if (!strcmp(argv[1], "stream")) {
        cancel_on_read = true;
        assert(!finish_note()); clean_idle();
        assert(ends == 1 && !s_rec);
    } else if (!strcmp(argv[1], "next")) {
        mode = MUSE_MODE_THINKING; request_cancel();
        begin_turn(); /* a stale request must not cancel the next note */
        delivered_event = true; bool delivered;
        assert(!hatch_reply(&delivered, NULL) && delivered && !cancels && !s_turn_cancelled);
    } else if (!strcmp(argv[1], "gesture")) {
        mode = MUSE_MODE_THINKING;
        uint32_t captured = muse_voice_thinking_turn(); assert(captured);
        begin_turn(); /* a delayed click from turn A is delivered during turn B */
        assert(muse_voice_thinking_turn() != captured);
        assert(!muse_voice_cancel_thinking(captured));
        assert(!cancel_thinking() && !cancels);
        request_cancel(); assert(cancel_thinking()); clean_idle();
    } else assert(false);
    return 0;
}
'''


class VoiceCancelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cc = shlex.split(os.environ.get("CC", "cc"))
        if not cc or not shutil.which(cc[0]):
            raise unittest.SkipTest("C compiler unavailable")
        voice = (MUSE / "muse_voice.c").read_text()
        feed_enum = re.search(r"typedef enum \{\n    FED,.*?\} feed_t;", voice, re.S).group()
        control = re.search(r"#define TURN_OPEN .*?static bool s_turn_cancelled;", voice, re.S).group()
        names = ("pre_reset", "mic_current", "go_idle", "begin_turn", "muse_voice_thinking_turn", "muse_voice_cancel_thinking",
                 "cancel_turn", "finish_turn", "cancel_thinking", "photo_complete", "hatch_reply", "feed_rest", "wait_delivered",
                 "drop_rec", "drop_oldest", "finish_note", "send_held")
        cls.tmp = tempfile.TemporaryDirectory()
        source = Path(cls.tmp.name) / "voice_cancel.c"
        source.write_text(FAKES + control + "\n" + feed_enum + "\n" + "\n".join(function(voice, name) for name in names) + MAIN)
        cls.binary = Path(cls.tmp.name) / "voice_cancel"
        result = subprocess.run([*cc, "-std=c11", "-Wall", "-Wextra", "-Werror",
                                 "-fsanitize=address,undefined", str(source), "-o", str(cls.binary)],
                                capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def check(self, case):
        result = subprocess.run([str(self.binary), case], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_cancel_is_only_accepted_while_thinking(self):
        self.check("reject")

    def test_stuck_thinking_recovers_with_mic_off(self):
        self.check("bench")

    def test_photo_wait_cancel_closes_stream_and_callbacks_once(self):
        self.check("photo")

    def test_acked_photo_cancel_keeps_success_callback(self):
        self.check("photo-acked")

    def test_cancel_during_audio_read_never_plays_arriving_audio(self):
        self.check("audio")

    def test_cancel_upload_drops_current_note_without_retry(self):
        self.check("upload")

    def test_cancel_and_mic_mute_together_still_drop_the_note(self):
        self.check("upload-muted")

    def test_cancel_reply_drops_note_without_new_recording(self):
        self.check("reply")

    def test_cancel_during_text_only_reply_linger_is_responsive(self):
        self.check("text")

    def test_cancel_saved_upload_preserves_other_notes(self):
        self.check("saved")

    def test_stale_cancel_does_not_affect_next_turn(self):
        self.check("next")

    def test_delayed_gesture_from_previous_turn_is_rejected(self):
        self.check("gesture")

    def test_accepted_cancel_before_ownership_decision_cannot_save_note(self):
        self.check("boundary")

    def test_cancel_after_ownership_decision_is_rejected(self):
        self.check("late")

    def test_cancel_after_saved_note_retry_decision_is_rejected(self):
        self.check("saved-late")

    def test_terminal_photo_callback_cannot_accept_a_late_cancel(self):
        self.check("photo-final")

    def test_cancel_from_photo_ack_is_honored_without_second_callback(self):
        self.check("photo-ack-cancel")

    def test_streaming_note_without_saved_buffer_finalizes_cancel(self):
        self.check("stream")

    def test_cancel_coinciding_with_reply_error_never_saves_note(self):
        self.check("reply-error")

    def test_cancel_coinciding_with_upload_error_never_saves_note(self):
        self.check("upload-error")

    def test_cancel_coinciding_with_saved_note_error_never_retries(self):
        self.check("saved-error")


if __name__ == "__main__":
    unittest.main()
