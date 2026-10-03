/* Copyright (c) Meta Platforms, Inc. and affiliates.
 * Licensed under the Apache License, Version 2.0. */
#include <assert.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "watcher_camera_fakes.h"
#include "../components/muse/boards/watcher_camera.c"

struct fake_queue { int items[4]; unsigned count; };
static int mutex;
static bool in_ui, fail_alloc, reject_send, inline_send;
static esp_err_t start_error;
static unsigned starts, stops, fresh_captures, sends, draws, hides;
static uint8_t rendered, sent_bytes[16];
static size_t sent_len;
static camera_frame_cb_t stream_cb;
static void *stream_ctx;
static muse_photo_sent_cb_t sent_cb;
static void *sent_ctx;
static char caption[128];
static int64_t now_us;
static unsigned failure_logs, success_logs;

int64_t esp_timer_get_time(void) { return now_us; }
void fake_camera_log(const char *tag, const char *format, ...)
{
    (void)tag;
    if (strstr(format, "frame failure")) failure_logs++;
    if (strstr(format, "first frame")) success_logs++;
}

const char *esp_err_to_name(esp_err_t err) { (void)err; return "fake error"; }
void *heap_caps_malloc(size_t len, unsigned caps)
{
    (void)caps;
    assert(!in_ui);
    if (fail_alloc) { fail_alloc = false; return NULL; }
    return malloc(len);
}
QueueHandle_t xQueueCreate(unsigned count, unsigned size)
{
    assert(count == 4 && size == sizeof(int));
    return calloc(1, sizeof(struct fake_queue));
}
int xQueueSend(QueueHandle_t q, const void *item, unsigned wait)
{
    assert(wait == 0);
    if (q->count == 4) return 0;
    q->items[q->count++] = *(const int *)item;
    return pdTRUE;
}
int xQueueReceive(QueueHandle_t q, void *item, unsigned wait)
{
    (void)wait;
    if (!q->count) return 0;
    *(int *)item = q->items[0];
    memmove(q->items, q->items + 1, --q->count * sizeof(int));
    return pdTRUE;
}
void vQueueDelete(QueueHandle_t q) { free(q); }
SemaphoreHandle_t xSemaphoreCreateMutex(void) { return &mutex; }
int xSemaphoreTake(SemaphoreHandle_t sem, unsigned wait)
{
    (void)wait;
    assert(!in_ui && !*sem);
    *sem = 1;
    return pdTRUE;
}
int xSemaphoreGive(SemaphoreHandle_t sem) { assert(*sem); *sem = 0; return pdTRUE; }
int xTaskCreateWithCaps(void (*task)(void *), const char *name, unsigned stack,
                        void *arg, unsigned priority, void *handle, unsigned caps)
{
    (void)task; (void)name; (void)stack; (void)arg;
    (void)priority; (void)handle; (void)caps;
    return pdPASS; /* Tests step the actual worker synchronously. */
}

static bool display_lock(int timeout) { (void)timeout; assert(!in_ui); return true; }
static void display_unlock(void) {}
static const muse_board_t board = { display_lock, display_unlock };
const muse_board_t *muse_board = &board;
void muse_state_set_caption(const char *format, ...)
{
    va_list ap;
    va_start(ap, format);
    vsnprintf(caption, sizeof(caption), format, ap);
    va_end(ap);
}
bool muse_ui_image_draw(int x, int y, int w, int h, const uint16_t *pixels)
{
    (void)x; (void)y; (void)w; (void)h;
    assert(!in_ui);
    /* The fake decoder draws one red pixel. Recover its 5-bit marker. */
    rendered = ((const uint8_t *)pixels)[0] >> 3;
    draws++;
    return true;
}
void muse_ui_image_hide(void) { assert(!in_ui); hides++; rendered = 0; }
void muse_ui_camera_hint(bool visible) { assert(!in_ui); (void)visible; }
void muse_ui_show_face(void) { assert(!in_ui); }

/* A tiny fixture format: FF D8, a pixel marker, then an optional failure flag.
 * The decoder draws before reporting errors, as a real decoder can. */
int jd_prepare(JDEC *jd, UINT (*read)(JDEC *, BYTE *, UINT), void *pool,
                UINT size, void *source)
{
    (void)read; (void)pool; (void)size;
    jd->device = source;
    jd->width = jd->height = 412;
    return JDR_OK;
}
int jd_decomp(JDEC *jd, UINT (*draw)(JDEC *, void *, JRECT *), unsigned scale)
{
    (void)scale;
    jpeg_source_t *src = jd->device;
    assert(src->length >= 4);
    uint8_t rgb[] = { (uint8_t)(src->data[2] << 3), 0, 0 };
    JRECT rect = {0};
    if (!draw(jd, rgb, &rect)) return 1;
    return src->data[3] ? 1 : JDR_OK;
}
int mbedtls_base64_encode(unsigned char *dst, size_t cap, size_t *written,
                          const unsigned char *src, size_t len)
{
    /* Identity is all these lifecycle tests need from the codec. */
    assert(len >= 4 && cap >= 3);
    snprintf((char *)dst, cap, "%02u", src[2]);
    *written = 2;
    return 0;
}

esp_err_t camera_stream_start(camera_frame_cb_t cb, void *ctx)
{
    assert(!in_ui);
    starts++;
    stream_cb = cb;
    stream_ctx = ctx;
    return start_error;
}
void camera_stream_stop(void) { assert(!in_ui && !mutex); stops++; }
esp_err_t camera_capture(camera_frame_t *frame)
{
    static const uint8_t jpeg[] = {0xff, 0xd8, 25, 0, 0xff, 0xd9};
    assert(!in_ui);
    fresh_captures++;
    *frame = (camera_frame_t){ .jpeg = jpeg, .len = sizeof(jpeg) };
    return ESP_OK;
}
void camera_release(camera_frame_t *frame) { (void)frame; }
bool muse_voice_send_photo(const uint8_t *jpeg, size_t len,
                           muse_photo_sent_cb_t cb, void *ctx)
{
    assert(!in_ui && watcher_camera_state() == WATCHER_CAMERA_SENDING);
    sends++;
    if (reject_send) return false;
    assert(len <= sizeof(sent_bytes));
    memcpy(sent_bytes, jpeg, len);
    sent_len = len;
    sent_cb = cb;
    sent_ctx = ctx;
    if (inline_send) cb(true, NULL, ctx);
    return true;
}

static void ui(void (*action)(void))
{
    in_ui = true;
    action();
    in_ui = false;
}
static void step(void)
{
    action_t action = ACTION_WAKE;
    xQueueReceive(s_actions, &action, 0);
    process_action(action);
}
static void frame(uint8_t marker, bool invalid)
{
    uint8_t jpeg[] = {0xff, 0xd8, marker, invalid, 0xff, 0xd9};
    camera_frame_t f = { .jpeg = jpeg, .len = sizeof(jpeg) };
    stream_cb(&f, stream_ctx);
    memset(jpeg, 0, sizeof(jpeg)); /* Backend releases input immediately. */
}
static void review(uint8_t marker)
{
    ui(watcher_camera_preview_toggle);
    step();
    assert(watcher_camera_state() == WATCHER_CAMERA_LIVE);
    frame(marker, false);
    ui(watcher_camera_preview_toggle);
    step();
    assert(watcher_camera_state() == WATCHER_CAMERA_REVIEW);
    assert(rendered == marker && s_last[2] == marker);
}
static void status_contains(const char *text)
{
    char error[CAMERA_ERROR_CAP];
    in_ui = true;
    watcher_camera_status(error, sizeof(error));
    in_ui = false;
    assert(strstr(error, text));
}
static void test_identity(void)
{
    ui(watcher_camera_preview_toggle);
    assert(starts == 0); /* UI never starts or stops camera synchronously. */
    step();
    frame(3, false);
    frame(19, false);
    assert(sends == 0);
    ui(watcher_camera_preview_toggle);
    assert(stops == 0);
    step();
    assert(stops == 1 && watcher_camera_state() == WATCHER_CAMERA_REVIEW);
    assert(rendered == 19 && s_last[2] == 19 && sends == 0);
    frame(8, false); /* A late callback cannot replace review. */
    assert(rendered == 19);
    ui(watcher_camera_send);
    ui(watcher_camera_send);
    assert(sends == 0);
    step();
    assert(sends == 1 && sent_len == 6 && sent_bytes[2] == 19);
    assert(s_last && watcher_camera_state() == WATCHER_CAMERA_SENDING);
    ui(watcher_camera_retake);
    ui(watcher_camera_preview_toggle);
    step();
    assert(starts == 1 && sends == 1 && s_last);
    sent_cb(true, NULL, sent_ctx);
    assert(s_last); /* Callback never frees UI-owned data itself. */
    step();
    assert(!s_last && watcher_camera_state() == WATCHER_CAMERA_CLOSED);
    assert(!strcmp(caption, "PHOTO SENT"));
}
static void test_retry(void)
{
    review(7);
    reject_send = true;
    ui(watcher_camera_send); step();
    assert(watcher_camera_state() == WATCHER_CAMERA_REVIEW && s_last[2] == 7);
    status_contains("CAN'T SEND");
    reject_send = false;
    ui(watcher_camera_send); step();
    muse_photo_sent_cb_t old_cb = sent_cb;
    void *old_ctx = sent_ctx;
    sent_cb(false, "offline", sent_ctx); step();
    assert(watcher_camera_state() == WATCHER_CAMERA_REVIEW && rendered == 7);
    status_contains("offline");
    ui(watcher_camera_send); step();
    old_cb(true, NULL, old_ctx); step();
    assert(watcher_camera_state() == WATCHER_CAMERA_SENDING && s_last[2] == 7);
    assert(sent_bytes[2] == 7);
    sent_cb(true, NULL, sent_ctx); step();
    assert(watcher_camera_state() == WATCHER_CAMERA_CLOSED);
}
static void test_frame_errors(void)
{
    ui(watcher_camera_preview_toggle); step();
    frame(11, false);
    assert(success_logs == 1);
    unsigned old_draws = draws;
    fail_alloc = true;
    frame(20, false);
    assert(failure_logs == 1);
    assert(draws == old_draws && rendered == 11 && s_last[2] == 11);
    frame(24, true);
    assert(failure_logs == 1); /* Repeated failures are rate limited. */
    assert(rendered == 11 && s_last[2] == 11); /* Partial decode restored. */
    uint8_t tiny[] = {0xff, 0xd8, 9, 0, 0xff, 0xd9};
    camera_frame_t oversized = { .jpeg = tiny, .len = MUSE_PHOTO_MAX_BYTES + 1 };
    stream_cb(&oversized, stream_ctx);
    now_us += CAMERA_ERROR_LOG_US;
    stream_cb(&oversized, stream_ctx);
    assert(failure_logs == 2); /* Large lengths never make diagnostics overread. */
    assert(rendered == 11 && s_last[2] == 11);
    ui(watcher_camera_preview_toggle); step();
    ui(watcher_camera_send); step();
    assert(sent_bytes[2] == 11);
    sent_cb(true, NULL, sent_ctx); step();
    ui(watcher_camera_preview_toggle); step();
    ui(watcher_camera_preview_toggle); step();
    assert(watcher_camera_state() == WATCHER_CAMERA_CLOSED);
    status_contains("NO CAMERA FRAME");
    ui(watcher_camera_send); step();
    assert(sends == 1);
}
static void test_cancel(void)
{
    ui(watcher_camera_preview_toggle);
    ui(watcher_camera_close); step();
    assert(!starts && watcher_camera_state() == WATCHER_CAMERA_CLOSED);
    review(5);
    void *old_ctx = stream_ctx;
    ui(watcher_camera_retake); step();
    assert(watcher_camera_state() == WATCHER_CAMERA_LIVE && !s_last);
    uint8_t jpeg[] = {0xff, 0xd8, 22, 0, 0xff, 0xd9};
    camera_frame_t stale = { .jpeg = jpeg, .len = sizeof(jpeg) };
    stream_cb(&stale, old_ctx);
    assert(!s_last);
    frame(13, false);
    ui(watcher_camera_preview_toggle); step();
    ui(watcher_camera_send); step();
    ui(watcher_camera_close); step();
    assert(watcher_camera_state() == WATCHER_CAMERA_SENDING && s_last[2] == 13);
    sent_cb(false, "cancelled", sent_ctx); step();
    assert(watcher_camera_state() == WATCHER_CAMERA_CLOSED && !s_last);
    ui(watcher_camera_preview_toggle); step();
    frame(17, false);
    ui(watcher_camera_close);
    assert(watcher_camera_state() == WATCHER_CAMERA_LIVE);
    step();
    assert(watcher_camera_state() == WATCHER_CAMERA_CLOSED && !s_last);
}
static void test_remote(void)
{
    char *b64;
    const char *error;
    review(9);
    assert(watcher_camera_capture(&b64, &error));
    assert(!strcmp(b64, "09") && fresh_captures == 0);
    free(b64);
    atomic_store(&s_busy, true);
    assert(!watcher_camera_capture(&b64, &error));
    assert(!strcmp(error, "camera busy"));
    ui(watcher_camera_close); step();
    assert(watcher_camera_state() == WATCHER_CAMERA_REVIEW); /* Close deferred. */
    atomic_store(&s_busy, false); step();
    assert(watcher_camera_state() == WATCHER_CAMERA_CLOSED);
    assert(watcher_camera_capture(&b64, &error));
    assert(!strcmp(b64, "25") && fresh_captures == 1);
    free(b64);
    review(18);
    ui(watcher_camera_send); step();
    assert(!watcher_camera_capture(&b64, &error) && !strcmp(error, "camera busy"));
    sent_cb(true, NULL, sent_ctx); step();
}
static void test_inline(void)
{
    start_error = ESP_ERR_TIMEOUT;
    ui(watcher_camera_preview_toggle); step();
    assert(watcher_camera_state() == WATCHER_CAMERA_CLOSED);
    status_contains("NOT READY");
    start_error = ESP_OK;
    review(6);
    inline_send = true;
    ui(watcher_camera_send); step();
    assert(watcher_camera_state() == WATCHER_CAMERA_SENDING);
    step();
    assert(watcher_camera_state() == WATCHER_CAMERA_CLOSED && !s_last);
}
int main(int argc, char **argv)
{
    assert(argc == 2);
    assert(watcher_camera_prepare() == ESP_OK);
    if (!strcmp(argv[1], "identity")) test_identity();
    else if (!strcmp(argv[1], "retry")) test_retry();
    else if (!strcmp(argv[1], "frame-errors")) test_frame_errors();
    else if (!strcmp(argv[1], "cancel")) test_cancel();
    else if (!strcmp(argv[1], "remote")) test_remote();
    else if (!strcmp(argv[1], "inline")) test_inline();
    else assert(false);
    ui(watcher_camera_close); step();
    assert(!s_last && !mutex && !atomic_load(&s_busy));
    vQueueDelete(s_actions);
    puts("ok");
    return 0;
}
