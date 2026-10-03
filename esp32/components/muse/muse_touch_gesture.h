/* Copyright (c) Meta Platforms, Inc. and affiliates.
 * Licensed under the Apache License, Version 2.0. */
#pragma once

#include <stdbool.h>
#include <stdint.h>

#define MUSE_TOUCH_HOLD_MS 400u
#define MUSE_TOUCH_DOUBLE_MS 350u
#define MUSE_TOUCH_DRAG_PX 18

enum {
    MUSE_TOUCH_PRESS = 1u,
    MUSE_TOUCH_MOVE = 2u,
    MUSE_TOUCH_RELEASE = 4u,
    MUSE_TOUCH_LOST = 8u,
};
enum {
    MUSE_TOUCH_RECORD = 1u,
    MUSE_TOUCH_SEND = 2u,
    MUSE_TOUCH_CANCEL = 4u,
    MUSE_TOUCH_PET = 8u,
    MUSE_TOUCH_CAMERA = 16u,
};
typedef struct {
    bool down, recording, waiting, second;
    uint32_t pressed_at, released_at;
    int x, y;
} muse_touch_gesture_t;

/* Called on pointer edges/movement and each UI tick. A drag, lost press, or
 * hidden home invalidates the whole gesture, including a pending single tap.
 * Only two completed taps open the camera: holding the second press records. */
static inline unsigned muse_touch_step(muse_touch_gesture_t *g, unsigned event,
                                      uint32_t now, int x, int y, bool enabled)
{
    if (!enabled || (event & MUSE_TOUCH_LOST)
        || (g->down && (event & MUSE_TOUCH_MOVE)
            && ((x - g->x) * (x - g->x) + (y - g->y) * (y - g->y)
                > MUSE_TOUCH_DRAG_PX * MUSE_TOUCH_DRAG_PX))) {
        unsigned out = g->recording ? MUSE_TOUCH_CANCEL : 0;
        *g = (muse_touch_gesture_t){0};
        return out;
    }
    unsigned out = 0;
    if (g->waiting && now - g->released_at > MUSE_TOUCH_DOUBLE_MS) {
        g->waiting = false;
        out |= MUSE_TOUCH_PET;
    }
    if ((event & MUSE_TOUCH_PRESS) && !g->down) {
        g->second = g->waiting;
        g->waiting = false;
        g->down = true;
        g->pressed_at = now;
        g->x = x;
        g->y = y;
    }
    if ((event & MUSE_TOUCH_RELEASE) && g->down) {
        if (g->recording) out |= MUSE_TOUCH_SEND;
        else if (now - g->pressed_at < MUSE_TOUCH_HOLD_MS) {
            if (g->second) out |= MUSE_TOUCH_CAMERA;
            else {
                g->waiting = true;
                g->released_at = now;
            }
        }
        g->down = g->recording = g->second = false;
    }
    if (g->down && !g->recording && now - g->pressed_at >= MUSE_TOUCH_HOLD_MS) {
        g->recording = true;
        g->second = false;
        out |= MUSE_TOUCH_RECORD;
    }
    return out;
}
