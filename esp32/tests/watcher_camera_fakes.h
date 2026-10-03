/* Copyright (c) Meta Platforms, Inc. and affiliates.
 * Licensed under the Apache License, Version 2.0. */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef int esp_err_t;
#define ESP_OK 0
#define ESP_ERR_NO_MEM 0x101
#define ESP_ERR_NOT_SUPPORTED 0x106
#define ESP_ERR_TIMEOUT 0x107
const char *esp_err_to_name(esp_err_t err);
void fake_camera_log(const char *tag, const char *format, ...);
#define ESP_LOGW(tag, ...) fake_camera_log(tag, __VA_ARGS__)
#define ESP_LOGI(tag, ...) fake_camera_log(tag, __VA_ARGS__)
int64_t esp_timer_get_time(void);
#define MALLOC_CAP_SPIRAM 1
#define MALLOC_CAP_8BIT 2
void *heap_caps_malloc(size_t len, unsigned caps);

typedef struct fake_queue *QueueHandle_t;
typedef int *SemaphoreHandle_t;
typedef int portMUX_TYPE;
#define portMUX_INITIALIZER_UNLOCKED 0
#define portENTER_CRITICAL(lock) ((void)(lock))
#define portEXIT_CRITICAL(lock) ((void)(lock))
#define pdTRUE 1
#define pdPASS 1
#define portMAX_DELAY UINT32_MAX
#define pdMS_TO_TICKS(ms) (ms)
QueueHandle_t xQueueCreate(unsigned count, unsigned size);
int xQueueSend(QueueHandle_t queue, const void *item, unsigned wait);
int xQueueReceive(QueueHandle_t queue, void *item, unsigned wait);
void vQueueDelete(QueueHandle_t queue);
SemaphoreHandle_t xSemaphoreCreateMutex(void);
int xSemaphoreTake(SemaphoreHandle_t mutex, unsigned wait);
int xSemaphoreGive(SemaphoreHandle_t mutex);
int xTaskCreateWithCaps(void (*task)(void *), const char *name, unsigned stack,
                        void *arg, unsigned priority, void *handle, unsigned caps);

typedef struct {
    bool (*display_lock)(int timeout);
    void (*display_unlock)(void);
} muse_board_t;
extern const muse_board_t *muse_board;
void muse_state_set_caption(const char *format, ...);
bool muse_ui_image_draw(int x, int y, int w, int h, const uint16_t *pixels);
void muse_ui_image_hide(void);
void muse_ui_camera_hint(bool visible);
void muse_ui_show_face(void);
typedef void (*muse_photo_sent_cb_t)(bool sent, const char *error, void *ctx);
#define MUSE_PHOTO_MAX_BYTES (192 * 1024)
bool muse_voice_send_photo(const uint8_t *jpeg, size_t len,
                           muse_photo_sent_cb_t cb, void *ctx);
int mbedtls_base64_encode(unsigned char *dst, size_t cap, size_t *written,
                          const unsigned char *src, size_t len);

typedef unsigned UINT;
typedef uint8_t BYTE;
typedef struct { void *device; unsigned width, height; } JDEC;
typedef struct { unsigned left, top, right, bottom; } JRECT;
#define JDR_OK 0
int jd_prepare(JDEC *decoder, UINT (*read)(JDEC *, BYTE *, UINT), void *pool,
                UINT size, void *source);
int jd_decomp(JDEC *decoder, UINT (*draw)(JDEC *, void *, JRECT *), unsigned scale);
