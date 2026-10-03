/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>

#define MUSE_WHEEL_HOLD_MS 300u
#define MUSE_WHEEL_DOUBLE_MS 350u
enum {
    MUSE_WHEEL_DOWN = 1u,
    MUSE_WHEEL_UP = 2u,
    MUSE_WHEEL_CAMERA = 4u,
    MUSE_WHEEL_TAP = 8u,
    MUSE_WHEEL_TURN = 16u,
};

typedef struct {
    bool down, ptt, consume, waiting, camera;
    uint32_t pressed_at, released_at;
} muse_wheel_gesture_t;

static inline unsigned muse_wheel_release(muse_wheel_gesture_t *g, uint32_t now)
{
    unsigned out = g->ptt ? MUSE_WHEEL_UP : 0;
    g->waiting = !g->ptt && !g->consume && now - g->pressed_at < MUSE_WHEEL_HOLD_MS;
    g->released_at = now;
    g->down = g->ptt = g->consume = false;
    return out;
}

/* Called on edges AND idle polls: only a continuous hold may start audio.
 * Screen-wake/menu/pairing presses bypass this recognizer in muse_input.c.
 * Unsigned elapsed arithmetic remains valid across the millisecond wrap. */
static inline unsigned muse_wheel_step(muse_wheel_gesture_t *g, unsigned edges,
                                       uint32_t now, bool camera)
{
    unsigned out = 0;
    if (camera != g->camera) {
        if (g->ptt) out |= MUSE_WHEEL_UP;
        bool held = g->down;
        *g = (muse_wheel_gesture_t){ .down = held, .consume = held, .camera = camera };
    }
    /* Turning changes the highlighted action. A click from before that
     * movement must never confirm the new selection (especially Send). */
    if (edges & MUSE_WHEEL_TURN) {
        g->waiting = false;
        if (g->down) g->consume = true;
    }
    if (g->waiting && now - g->released_at > MUSE_WHEEL_DOUBLE_MS) {
        g->waiting = false;
        out |= MUSE_WHEEL_TAP;
    }
    bool release = edges & MUSE_WHEEL_UP;
    if (g->down && release) {
        out |= muse_wheel_release(g, now);
        release = false;
    }
    if ((edges & MUSE_WHEEL_DOWN) && !g->down) {
        g->consume = g->waiting && now - g->released_at <= MUSE_WHEEL_DOUBLE_MS;
        if (g->consume) out |= MUSE_WHEEL_CAMERA;
        g->waiting = false;
        g->down = true;
        g->pressed_at = now;
    }
    if ((edges & MUSE_WHEEL_TURN) && g->down) g->consume = true;
    if (release && g->down) out |= muse_wheel_release(g, now);
    if (g->down && !g->consume && !g->ptt && !camera &&
        now - g->pressed_at >= MUSE_WHEEL_HOLD_MS) {
        g->ptt = true;
        out |= MUSE_WHEEL_DOWN;
    }
    return out;
}
