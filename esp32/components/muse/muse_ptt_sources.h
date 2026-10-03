/* Copyright (c) Meta Platforms, Inc. and affiliates.
 * Licensed under the Apache License, Version 2.0. */
#pragma once

#include <stdbool.h>

enum { MUSE_PTT_WHEEL = 1u, MUSE_PTT_TOUCH = 2u, MUSE_PTT_SERIAL = 4u };
enum { MUSE_PTT_ACTION_NONE, MUSE_PTT_ACTION_DOWN, MUSE_PTT_ACTION_UP, MUSE_PTT_ACTION_CANCEL };
typedef struct {
    unsigned held;
    bool sent, ending, cancelled, wake;
} muse_ptt_sources_t;

/* Runs only on the input task. Overlapping sources form one held session;
 * releasing or losing one finger cannot stop a still-held physical wheel. */
static inline void muse_ptt_source_set(muse_ptt_sources_t *s, unsigned source,
                                      bool down, bool cancel, bool wake)
{
    unsigned was = s->held;
    s->held = down ? was | source : was & ~source;
    if (!was && s->held) s->wake = wake;
    if (was && !s->held && s->sent && !s->ending) {
        s->ending = true;
        s->cancelled = cancel;
    }
}

/* Peek/commit keeps a release pending if the voice queue is briefly full.
 * Finish a previous session before starting a newly held source. */
static inline unsigned muse_ptt_source_next(const muse_ptt_sources_t *s)
{
    if (s->ending) return s->cancelled ? MUSE_PTT_ACTION_CANCEL : MUSE_PTT_ACTION_UP;
    if (s->held && !s->sent) return MUSE_PTT_ACTION_DOWN;
    return MUSE_PTT_ACTION_NONE;
}

static inline void muse_ptt_source_commit(muse_ptt_sources_t *s, unsigned action)
{
    if (action == MUSE_PTT_ACTION_DOWN) s->sent = true;
    else if (action != MUSE_PTT_ACTION_NONE) s->sent = s->ending = s->cancelled = false;
}
