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

#pragma once

#include <stdbool.h>
#include <stddef.h>
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    WATCHER_CAMERA_CLOSED,
    WATCHER_CAMERA_STARTING,
    WATCHER_CAMERA_LIVE,
    WATCHER_CAMERA_CAPTURING,
    WATCHER_CAMERA_REVIEW,
    WATCHER_CAMERA_SENDING,
} watcher_camera_state_t;

watcher_camera_state_t watcher_camera_state(void);
/* Copies the last camera/send error; safe to poll from the UI. */
void watcher_camera_status(char *error, size_t cap);

/* Nonblocking UI actions. Toggle opens, freezes, or retakes according to state.
 * REVIEW always owns a valid JPEG matching the frozen image. Send is explicit;
 * failed sends retain it for retry. Close during Send waits for its result,
 * because an accepted network request cannot be unsent. */
void watcher_camera_preview_toggle(void);
bool watcher_camera_preview_active(void);
void watcher_camera_send(void);
void watcher_camera_retake(void);
void watcher_camera_close(void);
/* Captures one frame for the Home Link camera.capture command. */
bool watcher_camera_capture(char **jpeg_base64, const char **error);
esp_err_t watcher_camera_prepare(void);

#ifdef __cplusplus
}
#endif
