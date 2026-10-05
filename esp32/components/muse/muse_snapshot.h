/* Copyright (c) contains-studio. Licensed under the Apache License, Version 2.0. */
#pragma once
#include "lvgl.h"

/* Called by LVGL with the display lock held. Capture once, then transfer an
 * owned immutable copy in the background. Requests while busy are ignored. */
void muse_snapshot_start(lv_obj_t *screen);
