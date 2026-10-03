// Modified by contains-studio for Muse Watcher (2026); see root CHANGES.md.
/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 */

#include "watcher_camera.h"

#include <stdatomic.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "camera.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/idf_additions.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "mbedtls/base64.h"
#include "muse_board.h"
#include "muse_state.h"
#include "muse_ui.h"
#include "muse_voice.h"
#include "rom/tjpgd.h"

static const char *TAG = "watcher.camera";
#define CAMERA_PREVIEW_SIZE 412
#define CAMERA_JPEG_POOL 3100
#define CAMERA_ERROR_CAP 96
#define CAMERA_ERROR_LOG_US (5 * 1000 * 1000)

/* The worker owns transitions and the immutable reviewed JPEG. Stream callbacks
 * replace s_last only after drawing that same copied JPEG successfully. */
static SemaphoreHandle_t s_lock;
static uint8_t *s_last;
static size_t s_last_len;
static atomic_int s_state = WATCHER_CAMERA_CLOSED;
static atomic_uint s_generation;
static atomic_bool s_busy;            /* excludes remote camera.capture */
static atomic_bool s_action_pending;
static atomic_bool s_close_requested;
static bool s_streaming;              /* worker only */
static bool s_close_after_send;       /* a submitted upload cannot be unsent */
/* Stream callbacks are joined before the worker decodes or opens again. */
static bool s_logged_frame, s_logged_failure;
static int64_t s_last_failure_us;

static portMUX_TYPE s_status_lock = portMUX_INITIALIZER_UNLOCKED;
static char s_error[CAMERA_ERROR_CAP];
typedef struct {
    bool pending, sent;
    unsigned generation;
    char error[CAMERA_ERROR_CAP];
} send_result_t;
static send_result_t s_result;

typedef enum { ACTION_WAKE, ACTION_OPEN, ACTION_FREEZE, ACTION_RETAKE, ACTION_SEND } action_t;
static QueueHandle_t s_actions;
static void camera_task(void *arg);

static void set_error(const char *error)
{
    portENTER_CRITICAL(&s_status_lock);
    strlcpy(s_error, error ? error : "", sizeof(s_error));
    portEXIT_CRITICAL(&s_status_lock);
}

watcher_camera_state_t watcher_camera_state(void)
{
    return (watcher_camera_state_t)atomic_load(&s_state);
}

void watcher_camera_status(char *error, size_t cap)
{
    if (!error || !cap) return;
    portENTER_CRITICAL(&s_status_lock);
    strlcpy(error, s_error, cap);
    portEXIT_CRITICAL(&s_status_lock);
}

typedef struct {
    const uint8_t *data;
    size_t length;
    size_t offset;
} jpeg_source_t;

typedef struct {
    const char *stage;
    int code;
    unsigned width, height;
    size_t read;
} preview_result_t;

static void log_frame_failure(const camera_frame_t *frame, preview_result_t result)
{
    int64_t now = esp_timer_get_time();
    if (s_logged_failure && now - s_last_failure_us < CAMERA_ERROR_LOG_US) return;
    s_logged_failure = true;
    s_last_failure_us = now;
    bool inspect = frame->jpeg && frame->len >= 2 && frame->len <= MUSE_PHOTO_MAX_BYTES;
    int soi = inspect ? frame->jpeg[0] == 0xff && frame->jpeg[1] == 0xd8 : -1;
    int end_eoi = inspect
        ? frame->jpeg[frame->len - 2] == 0xff && frame->jpeg[frame->len - 1] == 0xd9 : -1;
    int last_eoi = -1;
    if (inspect) {
        for (size_t i = frame->len; i >= 2; i--) {
            if (frame->jpeg[i - 2] == 0xff && frame->jpeg[i - 1] == 0xd9) {
                last_eoi = (int)(i - 2);
                break;
            }
        }
    }
    ESP_LOGW(TAG, "frame failure stage=%s code=%d bytes=%u camera=%dx%d soi=%d end_eoi=%d last_eoi=%d decoded=%ux%u read=%u",
             result.stage, result.code, (unsigned)frame->len, frame->width, frame->height,
             soi, end_eoi, last_eoi, result.width, result.height, (unsigned)result.read);
}

static UINT jpeg_read(JDEC *jd, BYTE *buffer, UINT count)
{
    jpeg_source_t *src = jd->device;
    size_t remain = src->length - src->offset;
    if (count > remain) count = remain;
    if (buffer && count) memcpy(buffer, src->data + src->offset, count);
    src->offset += count;
    return count;
}

static UINT jpeg_draw(JDEC *jd, void *bitmap, JRECT *rect)
{
    (void)jd;
    int left = rect->left;
    int top = rect->top;
    int right = rect->right > CAMERA_PREVIEW_SIZE - 1 ? CAMERA_PREVIEW_SIZE - 1 : rect->right;
    int bottom = rect->bottom > CAMERA_PREVIEW_SIZE - 1 ? CAMERA_PREVIEW_SIZE - 1 : rect->bottom;
    if (left > right || top > bottom) return 1;

    int source_w = rect->right - rect->left + 1;
    int width = right - left + 1;
    int height = bottom - top + 1;
    uint16_t pixels[16 * 16];
    uint8_t *pixel_bytes = (uint8_t *)pixels;
    const uint8_t *rgb = bitmap;
    for (int y = top; y <= bottom; y++) {
        for (int x = left; x <= right; x++) {
            size_t si = ((size_t)(y - rect->top) * source_w + (x - rect->left)) * 3;
            size_t di = ((size_t)(y - top) * width + (x - left)) * 2;
            uint16_t px = ((rgb[si] & 0xF8) << 8) | ((rgb[si + 1] & 0xFC) << 3) | (rgb[si + 2] >> 3);
            pixel_bytes[di] = px >> 8;
            pixel_bytes[di + 1] = px & 0xFF;
        }
    }
    return muse_ui_image_draw(left, top, width, height, pixels) ? 1 : 0;
}

static bool preview_draw(const uint8_t *jpeg, size_t len, preview_result_t *result)
{
    *result = (preview_result_t){ .stage = "alloc", .code = -1 };
    void *pool = heap_caps_malloc(CAMERA_JPEG_POOL, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!pool) return false;
    jpeg_source_t src = { .data = jpeg, .length = len };
    JDEC jd = {0};
    bool ok = false;
    result->stage = "prepare";
    result->code = jd_prepare(&jd, jpeg_read, pool, CAMERA_JPEG_POOL, &src);
    result->width = jd.width;
    result->height = jd.height;
    if (result->code == JDR_OK) {
        if (jd.width > 416 || jd.height > 416) {
            result->stage = "dimensions";
            muse_state_set_caption("CAMERA FRAME TOO LARGE");
        } else {
            result->stage = "decomp";
            result->code = jd_decomp(&jd, jpeg_draw, 0);
            ok = result->code == JDR_OK;
        }
    }
    result->read = src.offset;
    free(pool);
    return ok;
}


static bool accepts_frames(void)
{
    watcher_camera_state_t state = watcher_camera_state();
    return state == WATCHER_CAMERA_STARTING || state == WATCHER_CAMERA_LIVE;
}

static void on_frame(const camera_frame_t *frame, void *ctx)
{
    if ((unsigned)(uintptr_t)ctx != atomic_load(&s_generation) || !accepts_frames()) return;
    if (frame->len > MUSE_PHOTO_MAX_BYTES) {
        log_frame_failure(frame, (preview_result_t){ .stage = "size", .code = -1 });
        set_error("PHOTO TOO LARGE • TRY AGAIN");
        return;
    }
    if (!frame->jpeg || frame->len < 4 || frame->jpeg[0] != 0xff || frame->jpeg[1] != 0xd8
        || frame->jpeg[frame->len - 2] != 0xff || frame->jpeg[frame->len - 1] != 0xd9) {
        log_frame_failure(frame, (preview_result_t){ .stage = "framing", .code = -1 });
        set_error("CAMERA FRAME ERROR • TRY AGAIN");
        return;
    }
    uint8_t *copy = heap_caps_malloc(frame->len, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!copy) {
        log_frame_failure(frame, (preview_result_t){ .stage = "copy", .code = -1 });
        set_error("CAMERA MEMORY ERROR");
        return;  /* Never show a frame whose JPEG we could not retain. */
    }
    memcpy(copy, frame->jpeg, frame->len);
    preview_result_t result;
    if (!preview_draw(copy, frame->len, &result)) {
        log_frame_failure(frame, result);
        free(copy);
        set_error("CAMERA FRAME ERROR • TRY AGAIN");
        /* A decoder may have drawn a partial frame before failing. Restore the
         * retained frame so review can never refer to different visible pixels. */
        xSemaphoreTake(s_lock, portMAX_DELAY);
        if (s_last) preview_draw(s_last, s_last_len, &result);
        xSemaphoreGive(s_lock);
        return;
    }
    xSemaphoreTake(s_lock, portMAX_DELAY);
    free(s_last);
    s_last = copy;
    s_last_len = frame->len;
    xSemaphoreGive(s_lock);
    if (!s_logged_frame) {
        s_logged_frame = true;
        ESP_LOGI(TAG, "first frame bytes=%u camera=%dx%d decoded=%ux%u read=%u",
                 (unsigned)frame->len, frame->width, frame->height, result.width, result.height,
                 (unsigned)result.read);
    }
    set_error("");
}

/* Prepared once by board init, before UI or remote commands can run. */
esp_err_t watcher_camera_prepare(void)
{
    if (s_actions) return ESP_OK;
    if (!s_lock) s_lock = xSemaphoreCreateMutex();
    if (!s_lock) return ESP_ERR_NO_MEM;
    s_actions = xQueueCreate(4, sizeof(action_t));
    if (!s_actions) return ESP_ERR_NO_MEM;
    if (xTaskCreateWithCaps(camera_task, "camera_view", 8192, NULL, 4, NULL,
                            MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT) != pdPASS) {
        vQueueDelete(s_actions);
        s_actions = NULL;
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}

/* `jpeg` as base64, for the explicitly invoked camera.capture command. */
static char *to_base64(const uint8_t *jpeg, size_t len)
{
    size_t cap = (len + 2) / 3 * 4 + 1, out = 0;
    char *b64 = heap_caps_malloc(cap, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (b64 && mbedtls_base64_encode((unsigned char *)b64, cap, &out, jpeg, len) != 0) {
        free(b64);
        return NULL;
    }
    if (b64) b64[out] = '\0';
    return b64;
}

bool watcher_camera_capture(char **jpeg_base64, const char **error)
{
    if (!jpeg_base64 || !error) return false;
    *jpeg_base64 = NULL;
    *error = NULL;
    if (watcher_camera_prepare() != ESP_OK) {
        *error = "camera memory allocation failed";
        return false;
    }
    if (atomic_exchange(&s_busy, true)) {
        *error = "camera busy";
        return false;
    }
    watcher_camera_state_t state = watcher_camera_state();
    if (state == WATCHER_CAMERA_LIVE || state == WATCHER_CAMERA_REVIEW) {
        xSemaphoreTake(s_lock, portMAX_DELAY);
        bool had = s_last != NULL;
        *jpeg_base64 = had ? to_base64(s_last, s_last_len) : NULL;
        xSemaphoreGive(s_lock);
        *error = !had ? "no camera frame yet" : *jpeg_base64 ? NULL : "camera memory allocation failed";
    } else if (state != WATCHER_CAMERA_CLOSED) {
        *error = "camera busy";
    } else {
        camera_frame_t frame;
        esp_err_t err = camera_capture(&frame);
        if (err == ESP_OK) {
            *jpeg_base64 = to_base64(frame.jpeg, frame.len);
            camera_release(&frame);
            *error = *jpeg_base64 ? NULL : "camera memory allocation failed";
        } else {
            *error = err == ESP_ERR_TIMEOUT ? "Himax camera did not respond"
                     : err == ESP_ERR_NO_MEM ? "camera memory allocation failed"
                     : err == ESP_ERR_NOT_SUPPORTED ? "camera unavailable" : "camera capture failed";
        }
    }
    atomic_store(&s_busy, false);
    return *jpeg_base64 != NULL;
}

static void discard_frame(void)
{
    xSemaphoreTake(s_lock, portMAX_DELAY);
    free(s_last);
    s_last = NULL;
    s_last_len = 0;
    xSemaphoreGive(s_lock);
}

static void stop_stream(void)
{
    if (s_streaming) {
        /* Never hold s_lock or the display lock: stop joins the frame callback. */
        camera_stream_stop();
        s_streaming = false;
    }
}

static void close_view(const char *caption)
{
    atomic_store(&s_state, WATCHER_CAMERA_CAPTURING);
    atomic_fetch_add(&s_generation, 1);
    stop_stream();
    discard_frame();
    muse_ui_image_hide();
    muse_ui_camera_hint(false);
    muse_state_set_caption("%s", caption ? caption : "");
    s_close_after_send = false;
    set_error("");
    atomic_store(&s_state, WATCHER_CAMERA_CLOSED);
}

static void open_view(void)
{
    atomic_store(&s_state, WATCHER_CAMERA_STARTING);
    unsigned generation = atomic_fetch_add(&s_generation, 1) + 1;
    discard_frame();
    s_logged_frame = s_logged_failure = false;
    set_error("");
    muse_state_set_caption("%s", "");
    muse_ui_image_hide();
    muse_board->display_lock(-1);
    muse_ui_show_face();
    muse_board->display_unlock();
    esp_err_t err = camera_stream_start(on_frame, (void *)(uintptr_t)generation);
    if (err != ESP_OK) {
        const char *why = err == ESP_ERR_TIMEOUT ? "HIMAX CAMERA NOT READY" : "CAMERA PREVIEW FAILED";
        close_view(why);
        set_error(why);
        ESP_LOGW(TAG, "preview didn't start: %s", esp_err_to_name(err));
        return;
    }
    s_streaming = true;
    atomic_store(&s_state, WATCHER_CAMERA_LIVE);
}

static void freeze_view(void)
{
    atomic_store(&s_state, WATCHER_CAMERA_CAPTURING);
    stop_stream();
    /* The joined callback may have finished one final frame. Redraw the exact
     * retained JPEG, and only offer Send once that complete review is valid. */
    preview_result_t result = { .stage = "empty", .code = -1 };
    if (!s_last || !preview_draw(s_last, s_last_len, &result)) {
        camera_frame_t frame = { .jpeg = s_last, .len = s_last_len };
        log_frame_failure(&frame, result);
        const char *why = s_last ? "CAMERA FRAME ERROR • TRY AGAIN" : "NO CAMERA FRAME • TRY AGAIN";
        close_view(why);
        set_error(why);
        return;
    }
    set_error("");
    atomic_store(&s_state, WATCHER_CAMERA_REVIEW);
}

static void photo_sent(bool sent, const char *error, void *ctx)
{
    unsigned generation = (unsigned)(uintptr_t)ctx;
    portENTER_CRITICAL(&s_status_lock);
    if (generation == atomic_load(&s_generation) && watcher_camera_state() == WATCHER_CAMERA_SENDING) {
        s_result = (send_result_t){ .pending = true, .sent = sent, .generation = generation };
        strlcpy(s_result.error, error ? error : "", sizeof(s_result.error));
    }
    portEXIT_CRITICAL(&s_status_lock);
    action_t wake = ACTION_WAKE;
    xQueueSend(s_actions, &wake, 0);  /* A full queue already guarantees a wake. */
}

static void send_photo(void)
{
    if (!s_last || !s_last_len) return;
    unsigned generation = atomic_fetch_add(&s_generation, 1) + 1;
    set_error("");
    atomic_store(&s_state, WATCHER_CAMERA_SENDING);
    if (!muse_voice_send_photo(s_last, s_last_len, photo_sent, (void *)(uintptr_t)generation)) {
        set_error("CAN'T SEND PHOTO • CHECK CONNECTION AND TRY AGAIN");
        atomic_store(&s_state, WATCHER_CAMERA_REVIEW);
    }
}

static void process_result(void)
{
    portENTER_CRITICAL(&s_status_lock);
    send_result_t result = s_result;
    s_result.pending = false;
    portEXIT_CRITICAL(&s_status_lock);
    if (!result.pending || result.generation != atomic_load(&s_generation)
        || watcher_camera_state() != WATCHER_CAMERA_SENDING) return;
    if (result.sent) {
        close_view("PHOTO SENT");
    } else if (s_close_after_send) {
        close_view("");
    } else {
        set_error(result.error[0] ? result.error : "PHOTO NOT SENT • TRY AGAIN");
        atomic_store(&s_state, WATCHER_CAMERA_REVIEW);
    }
}

/* One iteration is kept separate for deterministic host lifecycle tests. */
static void process_action(action_t action)
{
    if (atomic_exchange(&s_busy, true)) {
        if (action != ACTION_WAKE) set_error("CAMERA BUSY • TRY AGAIN");
        if (action != ACTION_WAKE) atomic_store(&s_action_pending, false);
        return;
    }
    if (atomic_exchange(&s_close_requested, false)) {
        if (watcher_camera_state() == WATCHER_CAMERA_SENDING) {
            s_close_after_send = true;
        } else {
            close_view("");
        }
        /* Discard actions queued before Cancel, including an unprocessed Open. */
        action_t discard;
        while (xQueueReceive(s_actions, &discard, 0) == pdTRUE) {}
        atomic_store(&s_action_pending, false);
        action = ACTION_WAKE;
    }
    process_result();
    watcher_camera_state_t state = watcher_camera_state();
    if (action == ACTION_OPEN && state == WATCHER_CAMERA_CLOSED) open_view();
    else if (action == ACTION_FREEZE && state == WATCHER_CAMERA_LIVE) freeze_view();
    else if (action == ACTION_RETAKE && state == WATCHER_CAMERA_REVIEW) open_view();
    else if (action == ACTION_SEND && state == WATCHER_CAMERA_REVIEW) send_photo();
    if (action != ACTION_WAKE) atomic_store(&s_action_pending, false);
    atomic_store(&s_busy, false);
}

static void camera_task(void *arg)
{
    (void)arg;
    for (;;) {
        action_t action = ACTION_WAKE;
        xQueueReceive(s_actions, &action, pdMS_TO_TICKS(100));
        process_action(action);
    }
}

/* UI callbacks never wait for a mutex, camera shutdown, JPEG work, or upload. */
static void post_action(action_t action)
{
    if (!s_actions || atomic_exchange(&s_action_pending, true)) return;
    if (xQueueSend(s_actions, &action, 0) != pdTRUE) atomic_store(&s_action_pending, false);
}

bool watcher_camera_preview_active(void)
{
    return watcher_camera_state() == WATCHER_CAMERA_LIVE;
}

void watcher_camera_preview_toggle(void)
{
    switch (watcher_camera_state()) {
    case WATCHER_CAMERA_CLOSED: post_action(ACTION_OPEN); break;
    case WATCHER_CAMERA_LIVE: post_action(ACTION_FREEZE); break;
    case WATCHER_CAMERA_REVIEW: post_action(ACTION_RETAKE); break;
    default: break;
    }
}

void watcher_camera_send(void)
{
    if (watcher_camera_state() == WATCHER_CAMERA_REVIEW) post_action(ACTION_SEND);
}

void watcher_camera_retake(void)
{
    if (watcher_camera_state() == WATCHER_CAMERA_REVIEW) post_action(ACTION_RETAKE);
}

void watcher_camera_close(void)
{
    if (!s_actions) return;
    atomic_store(&s_close_requested, true);
    action_t wake = ACTION_WAKE;
    xQueueSend(s_actions, &wake, 0);
}
