/* Copyright (c) contains-studio. Licensed under the Apache License, Version 2.0. */
#pragma once

enum {
    MUSE_WEATHER_HIGH = 1, MUSE_WEATHER_LOW = 2,
    MUSE_WEATHER_WIND = 4, MUSE_WEATHER_HUMIDITY = 8,
};

/* Copied by value; no downloaded image or borrowed command strings. */
typedef struct {
    float temp_f, high_f, low_f, wind_mph, humidity_pct;
    unsigned present;
    char outfit_id[32];
} muse_weather_card_t;
