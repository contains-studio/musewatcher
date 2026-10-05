/* Copyright (c) contains-studio. Licensed under the Apache License, Version 2.0. */
#include "muse_snapshot.h"
#include "muse_console.h"
#include "sdkconfig.h"

#if LV_USE_SNAPSHOT
#include <stdatomic.h>
#include <stdio.h>
#include <string.h>
#include "esp_heap_caps.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "mbedtls/base64.h"

#if CONFIG_SPIRAM && CONFIG_FREERTOS_TASK_CREATE_ALLOW_EXT_MEM && CONFIG_SPIRAM_XIP_FROM_PSRAM
#include "freertos/idf_additions.h"
#define EXTERNAL_STACK 1
#else
#define EXTERNAL_STACK 0
#endif

#define SNAP_STACK 4096
#define INTERNAL_CAPS (MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT)
static atomic_bool s_busy;
typedef struct {
    unsigned width, height;
    unsigned char pixels[];
} snapshot_t;

static void send_snapshot(void *arg)
{
    snapshot_t *snap = arg;
    enum { RAW = 144 }; /* Encoded lines fit the 256-byte TX ring. */
    char header[48];
    int n = snprintf(header, sizeof(header), "\nSNAP BEGIN %u %u %d\n", snap->width, snap->height, RAW);
    bool ok = muse_console_write_timeout(header, (size_t)n, 1000);
    unsigned char encoded[4 * RAW / 3 + 4];
    for (unsigned y = 0; ok && y < snap->height; y++) {
        const unsigned char *p = snap->pixels + y * snap->width * 2;
        size_t left = snap->width * 2;
        while (ok && left) {
            size_t chunk = left > RAW ? RAW : left, len = 0;
            ok = mbedtls_base64_encode(encoded, sizeof(encoded), &len, p, chunk) == 0;
            if (ok) {
                encoded[len++] = '\n';
                ok = muse_console_write_timeout(encoded, len, 1000);
            }
            p += chunk;
            left -= chunk;
        }
    }
    if (ok) muse_console_write_timeout("SNAP END\n", 9, 1000);
    heap_caps_free(snap);
    atomic_store(&s_busy, false);
#if EXTERNAL_STACK
    vTaskDeleteWithCaps(NULL);
#else
    vTaskDelete(NULL);
#endif
}
#endif

void muse_snapshot_start(lv_obj_t *screen)
{
#if LV_USE_SNAPSHOT
    bool expected = false;
    if (!atomic_compare_exchange_strong(&s_busy, &expected, true)) return;
    // WithCaps deletion needs an internal cleanup task even with a PSRAM stack.
    size_t reserve = 4096 + (EXTERNAL_STACK ? 0 : SNAP_STACK);
    if (heap_caps_get_free_size(INTERNAL_CAPS) < reserve
        || heap_caps_get_largest_free_block(INTERNAL_CAPS) < 4096) goto failed;
    lv_draw_buf_t *buf = lv_snapshot_take(screen, LV_COLOR_FORMAT_RGB565);
    if (!buf) goto failed;
    size_t row = (size_t)buf->header.w * 2;
    snapshot_t *snap = heap_caps_malloc(sizeof(*snap) + row * buf->header.h,
#if CONFIG_SPIRAM
                                       MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
#else
                                       MALLOC_CAP_8BIT);
#endif
    if (snap) {
        snap->width = buf->header.w;
        snap->height = buf->header.h;
        for (unsigned y = 0; y < snap->height; y++)
            memcpy(snap->pixels + y * row, buf->data + y * buf->header.stride, row);
    }
    // No LVGL objects escape the UI thread; the worker can finish even asleep.
    lv_draw_buf_destroy(buf);
    if (!snap) goto failed;
#if EXTERNAL_STACK
    BaseType_t created = xTaskCreateWithCaps(send_snapshot, "snapshot", SNAP_STACK,
                         snap, 2, NULL, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
#else
    BaseType_t created = xTaskCreate(send_snapshot, "snapshot", SNAP_STACK, snap, 2, NULL);
#endif
    if (created == pdPASS) return;
    heap_caps_free(snap);
failed:
    atomic_store(&s_busy, false);
    muse_console_write_timeout("\nSNAP ERROR\n", 12, 0);
#else
    (void)screen;
    muse_console_write_timeout("\nSNAP OFF\n", 10, 0);
#endif
}
