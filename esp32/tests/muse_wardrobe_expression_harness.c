/* Copyright (c) Meta Platforms, Inc. and affiliates.
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
 * Distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND.
 */
#include <assert.h>
#include "../components/muse/muse_wardrobe.c"

static bool dark(uint16_t pixel)
{
    return (pixel >> 11) < 12 && ((pixel >> 5) & 63) < 24;
}

static void smooth_fill(void)
{
    uint16_t pixels[17];
    muse_wardrobe_asset_t asset = {"gradient", 17, 1, pixels};
    /* Each channel slopes independently, in both directions. Reconstructing
     * this ramp must not introduce the old two-flat-halves seam. */
    for (int reverse = 0; reverse < 2; reverse++) {
        for (int x = 0; x < 17; x++) {
            int v = reverse ? 16 - x : x;
            pixels[x] = (uint16_t)((8 + v) << 11 | (48 - 2 * v) << 5 | (4 + v));
        }
        for (int end = 11; end <= 12; end++)
            for (int x = 3; x <= end; x++)
                assert(face_fill(&asset, 3, end, x, 0) == pixels[x]);
    }
}

static void artwork_anchors(void)
{
    for (unsigned i = 0; i < MUSE_WARDROBE_ASSET_COUNT; i++) {
        const muse_wardrobe_asset_t *a = &muse_wardrobe_assets[i];
        const happy_rig_t *r = &s_rigs[i];
        assert(r->lx0 > 0 && r->lx1 < r->rx0 && r->rx1 + 1 < a->width);
        assert(r->mx0 > r->lx1 && r->mx1 < r->rx0);
        assert(r->ey0 < r->ey1 && r->ey1 < r->my0 && r->my0 < r->my1);
        assert(r->my1 < r->shoulder && r->shoulder <= r->arm_end && r->arm_end < a->height);
        if (r->arm_cuts) assert(sizeof(s_crochet_cuts) == (unsigned)(r->arm_end - r->shoulder + 1));
        unsigned marks = 0;
        /* The source eyes/mouth must fit inside the replacement regions.
         * This catches artwork changes which leave an old pupil or smile
         * below the new expression, even if the face changes overall. */
        for (int y = r->ey0 - 1; y <= r->my1 + 1; y++) {
            for (int x = r->lx0 - 1; x <= r->rx1 + 1; x++) {
                bool eye = y >= r->ey0 && y <= r->ey1 &&
                    ((x >= r->lx0 && x <= r->lx1) || (x >= r->rx0 && x <= r->rx1));
                bool mouth = x >= r->mx0 && x <= r->mx1 && y >= r->my0 && y <= r->my1;
                if (dark(a->pixels[y * a->width + x])) { assert(eye || mouth); marks++; }
                if (!eye && !mouth) {
                    s_arm_cos = 0; s_arm_sin = 256;
                    assert(happy_pixel(a, r, x, y) == a->pixels[y * a->width + x]);
                }
            }
        }
        assert(marks >= 12);
        const int edges[] = {r->lx0 - 1, r->lx1 + 1, r->rx0 - 1, r->rx1 + 1};
        for (unsigned e = 0; e < sizeof(edges) / sizeof(edges[0]); e++)
            for (int y = r->ey0; y <= r->ey1; y++)
                assert(!dark(a->pixels[y * a->width + edges[e]]));
        for (int y = r->my0; y <= r->my1; y++) {
            assert(!dark(a->pixels[y * a->width + r->mx0 - 1]));
            assert(!dark(a->pixels[y * a->width + r->mx1 + 1]));
        }
    }
}

static void sample_bounds(void)
{
    /* Inverse rotation can sample outside both the old and new silhouette.
     * Exercise both angular extremes under ASan/UBSan, including crops smaller
     * than stale anchors, without allocating a display-sized buffer. */
    volatile uint16_t sampled = 0;
    const int cosines[] = {-56, 0, 56};
    uint16_t tiny_pixels[9] = {1,2,3,4,5,6,7,8,9};
    muse_wardrobe_asset_t tiny = {"short crop", 3, 3, tiny_pixels};
    happy_rig_t stale = s_rigs[0];
    stale.shoulder = 1;
    for (unsigned c = 0; c < sizeof(cosines) / sizeof(cosines[0]); c++) {
        s_arm_cos = cosines[c]; s_arm_sin = c == 1 ? 256 : 250;
        for (unsigned i = 0; i <= MUSE_WARDROBE_ASSET_COUNT; i++) {
            const muse_wardrobe_asset_t *a = i < MUSE_WARDROBE_ASSET_COUNT ? &muse_wardrobe_assets[i] : &tiny;
            const happy_rig_t *r = i < MUSE_WARDROBE_ASSET_COUNT ? &s_rigs[i] : &stale;
            for (int y = -32; y < a->height + 32; y++)
                for (int x = -32; x < a->width + 32; x++) sampled = happy_pixel(a, r, x, y);
        }
    }
    (void)sampled;
}

int main(int argc, char **argv)
{
    assert(argc == 2);
    if (!strcmp(argv[1], "fill")) smooth_fill();
    else if (!strcmp(argv[1], "anchors")) artwork_anchors();
    else if (!strcmp(argv[1], "bounds")) sample_bounds();
    else return 2;
    return 0;
}
