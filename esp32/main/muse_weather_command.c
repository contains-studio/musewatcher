/* Copyright (c) contains-studio. Licensed under the Apache License, Version 2.0. */
#include "muse_weather_command.h"

#include <math.h>
#include <string.h>

#include "muse_ui.h"
#include "muse_wardrobe.h"
#include "muse_weather.h"

static cJSON *weather_error(const char *code, const char *message) {
    cJSON *result = cJSON_CreateObject();
    cJSON_AddBoolToObject(result, "ok", false);
    cJSON *error = cJSON_AddObjectToObject(result, "error");
    cJSON_AddStringToObject(error, "code", code);
    cJSON_AddStringToObject(error, "message", message);
    return result;
}

static bool number_in_range(const cJSON *item, double min, double max) {
    return cJSON_IsNumber(item) && isfinite(item->valuedouble) &&
           item->valuedouble >= min && item->valuedouble <= max;
}

cJSON *muse_weather_command(const cJSON *params) {
    if (!cJSON_IsObject(params)) {
        return weather_error("invalid_params", "expected a weather object");
    }

    muse_weather_card_t card = {0};
    const cJSON *temp = cJSON_GetObjectItemCaseSensitive(params, "temp_f");
    if (!number_in_range(temp, -238, 302)) {
        return weather_error("invalid_params", "temp_f must be a finite number from -238 to 302");
    }
    card.temp_f = (float)temp->valuedouble;

    const cJSON *outfit = cJSON_GetObjectItemCaseSensitive(params, "outfit_id");
    if (!cJSON_IsString(outfit) || !outfit->valuestring ||
        strlen(outfit->valuestring) >= sizeof(card.outfit_id) ||
        !muse_wardrobe_valid(outfit->valuestring)) {
        return weather_error("invalid_params", "outfit_id must be a supported outfit ID");
    }
    strcpy(card.outfit_id, outfit->valuestring);

    const struct {
        const char *key;
        double min, max;
        unsigned bit;
        float *value;
        const char *error;
    } optional[] = {
        {"high_f", -238, 302, MUSE_WEATHER_HIGH, &card.high_f,
         "high_f must be a finite number from -238 to 302"},
        {"low_f", -238, 302, MUSE_WEATHER_LOW, &card.low_f,
         "low_f must be a finite number from -238 to 302"},
        {"wind_mph", 0, 400, MUSE_WEATHER_WIND, &card.wind_mph,
         "wind_mph must be a finite number from 0 to 400"},
        {"humidity_pct", 0, 100, MUSE_WEATHER_HUMIDITY, &card.humidity_pct,
         "humidity_pct must be a finite number from 0 to 100"},
    };
    for (unsigned i = 0; i < sizeof(optional) / sizeof(optional[0]); i++) {
        const cJSON *item = cJSON_GetObjectItemCaseSensitive(params, optional[i].key);
        if (!item) continue;
        if (!number_in_range(item, optional[i].min, optional[i].max)) {
            return weather_error("invalid_params", optional[i].error);
        }
        *optional[i].value = (float)item->valuedouble;
        card.present |= optional[i].bit;
    }
    const cJSON *high = cJSON_GetObjectItemCaseSensitive(params, "high_f");
    const cJSON *low = cJSON_GetObjectItemCaseSensitive(params, "low_f");
    if (high && low && high->valuedouble < low->valuedouble) {
        return weather_error("invalid_params", "high_f must be at least low_f");
    }

    esp_err_t err = muse_ui_weather_show(&card);
    if (err != ESP_OK) {
        if (err == ESP_ERR_INVALID_STATE) {
            return weather_error("not_ready", "weather display is not ready");
        }
        return weather_error("storage_error", "could not save the weather outfit");
    }
    cJSON *result = cJSON_CreateObject();
    cJSON_AddBoolToObject(result, "ok", true);
    cJSON_AddBoolToObject(result, "accepted", true);
    cJSON_AddStringToObject(result, "outfit_id", card.outfit_id);
    return result;
}
