/* Copyright (c) Meta Platforms, Inc. and affiliates.
 * Licensed under the Apache License, Version 2.0. */
#pragma once

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Call after NVS initialization and before starting command producers.
 * Missing or invalid saved outfits use the default character. Storage errors
 * are returned with that same safe default, so wardrobe cannot block startup. */
esp_err_t muse_wardrobe_settings_init(void);

/* Persist a validated selection before publishing it to the renderer.
 * A failed save leaves the current character unchanged. */
esp_err_t muse_wardrobe_settings_set(const char *outfit_id);

#ifdef __cplusplus
}
#endif
