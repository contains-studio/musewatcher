/* Copyright (c) Meta Platforms, Inc. and affiliates.
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
 * Distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND.
 */
#pragma once

#include "muse_pixel.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Thread-safe selection. "default" restores the procedural avatar. Invalid
 * IDs (including NULL) return false and leave the selection unchanged. */
bool muse_wardrobe_valid(const char *id);
bool muse_wardrobe_select(const char *id);
const char *muse_wardrobe_current(void);  /* immutable storage; caller never frees */

/* Called by the UI once per frame. Selection changes are latched here, not
 * while decoding strips. Call before scaling that frame's regions. */
/* Ambient scenery and little hops are enabled only on the unobstructed idle
 * home. Replies, the browser and other active states keep the quieter pose. */
bool muse_wardrobe_render(const muse_pose_t *pose, bool ambient);

/* Same inclusive region/packed destination contract as muse_pixel_scale.
 * Returns false for a rendered "default" frame (dst is untouched), or invalid
 * arguments. Canvas and region dimensions must be between 1 and 512.
 * Out-of-canvas coordinates are filled black; row padding stays
 * untouched. A remote select cannot alter the already-rendered frame. */
bool muse_wardrobe_scale(uint16_t *dst, int stride_px, int x0, int x1,
                         int y0, int y1, int canvas_px);

#ifdef __cplusplus
}
#endif
