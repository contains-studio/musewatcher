/* Copyright (c) Meta Platforms, Inc. and affiliates.
 * Licensed under the Apache License, Version 2.0. */
#include "muse_wardrobe_settings.h"

#include <string.h>

#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "nvs.h"
#include "muse_wardrobe.h"

#define WARDROBE_NS "muse_wardrobe"
#define WARDROBE_KEY "outfit"
/* All catalog IDs, including their terminator, fit within this bound. */
#define OUTFIT_ID_SIZE 32

static const char *TAG = "muse_wardrobe";
static SemaphoreHandle_t s_lock;

esp_err_t muse_wardrobe_settings_init(void)
{
    if (!s_lock) s_lock = xSemaphoreCreateMutex();
    if (!s_lock) return ESP_ERR_NO_MEM;

    xSemaphoreTake(s_lock, portMAX_DELAY);
    muse_wardrobe_select("default");
    nvs_handle_t handle;
    esp_err_t err = nvs_open(WARDROBE_NS, NVS_READONLY, &handle);
    if (err == ESP_OK) {
        char outfit[OUTFIT_ID_SIZE];
        size_t size = sizeof(outfit);
        err = nvs_get_str(handle, WARDROBE_KEY, outfit, &size);
        nvs_close(handle);
        if (err == ESP_OK && !muse_wardrobe_select(outfit)) {
            ESP_LOGW(TAG, "Ignoring invalid saved outfit");
        }
    }
    if (err == ESP_ERR_NVS_NOT_FOUND || err == ESP_ERR_NVS_INVALID_LENGTH) {
        err = ESP_OK;
    }
    xSemaphoreGive(s_lock);
    return err;
}

esp_err_t muse_wardrobe_settings_set(const char *outfit_id)
{
    if (!outfit_id || !muse_wardrobe_valid(outfit_id)) return ESP_ERR_INVALID_ARG;
    if (!s_lock) return ESP_ERR_INVALID_STATE;

    xSemaphoreTake(s_lock, portMAX_DELAY);
    esp_err_t err = ESP_OK;
    if (strcmp(outfit_id, muse_wardrobe_current()) != 0) {
        nvs_handle_t handle;
        err = nvs_open(WARDROBE_NS, NVS_READWRITE, &handle);
        if (err == ESP_OK) {
            err = nvs_set_str(handle, WARDROBE_KEY, outfit_id);
            if (err == ESP_OK) err = nvs_commit(handle);
            nvs_close(handle);
        }
        if (err == ESP_OK) muse_wardrobe_select(outfit_id);
    }
    xSemaphoreGive(s_lock);
    return err;
}
