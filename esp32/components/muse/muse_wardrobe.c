/* Copyright (c) Meta Platforms, Inc. and affiliates.
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
 * Distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND.
 */
#include "muse_wardrobe.h"
#include "wardrobe_assets.h"

#include <math.h>
#include <stdatomic.h>
#include <string.h>

/* No frame-sized internal-RAM allocation.
 * A frame atomically packs index:4, left:7, top:7, width:7, height:7. Index 0
 * selects the original procedural avatar, independent of the requested ID. */
static atomic_uint s_selected;
static atomic_uint s_frame;
_Static_assert(MUSE_WARDROBE_ASSET_COUNT < 16, "frame index has four bits");
_Static_assert(MUSE_WARDROBE_GRID < 128, "frame dimensions have seven bits");

/* A small scene of hard-edged pixel rectangles, latched with the pose on the
 * UI thread. Decoder workers only read it until that frame has finished.
 * Remote outfit selection changes s_selected, never the rendered scene. */
typedef struct { int16_t x, y, w, h; uint16_t color; } pixel_rect_t;
static pixel_rect_t s_effects[96];
static unsigned s_effect_count;

/* Source-art anchors: inclusive eye/mouth bounds, then shoulder and arm end. These
 * move the original sleeves/fur instead of swapping away the chosen outfit.
 * Like scenery, the happy pose is latched on the UI thread for all strips. */
typedef struct {
    uint8_t lx0, lx1, rx0, rx1, ey0, ey1;
    uint8_t mx0, mx1, my0, my1;
    uint8_t shoulder, arm_end, arm_width, arm_taper;
    const uint8_t *arm_cuts; /* Optional silhouette widths from shoulder to end. */
} happy_rig_t;
/* The crochet sleeves flare around the red bands instead of tapering evenly. */
static const uint8_t s_crochet_cuts[] = {11,11,11,11,11,11,12,13,13,11,12,12,12,8,8,8,7};
static const happy_rig_t s_rigs[] = {
    {21,25,35,39,18,23,27,33,25,28,31,47,11,1,NULL}, /* mild-knit */
    {21,25,35,40,18,23,27,33,25,28,31,47,11,1,s_crochet_cuts}, /* warm-crochet */
    {21,25,35,39,18,23,27,33,25,28,31,47,11,1,NULL}, /* cool-suede */
    {19,23,33,37,23,27,25,31,29,32,35,50,11,1,NULL}, /* cold-layers */
    {21,25,35,39,19,23,27,33,25,28,31,47,11,1,NULL}, /* wind-shell */
    {19,23,32,36,21,25,25,31,27,30,34,48,11,1,NULL}, /* rain-shell */
    {20,24,34,39,21,26,26,32,28,31,34,50,11,1,NULL}, /* heatwave-linen */
    {20,24,34,38,22,27,26,32,29,32,36,52,10,0,NULL}, /* freezing-puffer */
    {18,23,32,37,21,25,25,31,27,30,35,50,11,1,NULL}, /* cold-rain-parka */
    {20,25,34,39,22,26,27,33,28,31,35,50,11,1,NULL}, /* fog-overshirt */
    {19,23,33,38,19,24,26,32,26,29,34,51,11,1,NULL}, /* warm-rain-shell */
    {21,25,35,40,21,25,28,34,27,30,35,50,11,1,NULL}, /* hot-wind-stripes */
    {17,22,32,37,19,24,24,30,26,29,35,50,11,1,NULL}, /* hot-shorts */
};
_Static_assert(sizeof(s_rigs) / sizeof(s_rigs[0]) == MUSE_WARDROBE_ASSET_COUNT,
               "each outfit needs happy-pose anchors");
static bool s_excited;
static int s_arm_cos, s_arm_sin;

#define RGB565(r, g, b) (((r) >> 3) << 11 | ((g) >> 2) << 5 | ((b) >> 3))
enum {
    GOLD = RGB565(255, 207, 96), PEACH = RGB565(255, 150, 120),
    PINK = RGB565(243, 145, 194), LILAC = RGB565(178, 156, 232),
    ICE = RGB565(169, 224, 240), BLUE = RGB565(101, 158, 202),
    LEAF = RGB565(157, 186, 118), SHADOW = RGB565(44, 38, 52),
};

static void rect(int x, int y, int w, int h, uint16_t color)
{
    if (s_effect_count < sizeof(s_effects) / sizeof(s_effects[0]))
        s_effects[s_effect_count++] = (pixel_rect_t){x, y, w, h, color};
}

static void star(int x, int y, int radius, uint16_t color)
{
    rect(x - radius, y, radius * 2 + 1, 1, color);
    rect(x, y - radius, 1, radius * 2 + 1, color);
}

static void cloud(int x, int y)
{
    rect(x + 3, y - 3, 7, 3, ICE);
    rect(x, y, 16, 4, ICE);
    rect(x + 2, y + 4, 12, 2, BLUE);
}

static void scenery(const char *id, float t, int hop, float happy)
{
    /* Ground contact makes the little jumps read as intentional movement. */
    rect(36 + hop / 2, 91, 24 - hop, 2, SHADOW);
    rect(40 + hop / 2, 93, 16 - hop, 1, SHADOW);
    bool rain = strstr(id, "rain") != NULL;
    bool wind = strstr(id, "wind") != NULL;
    bool snow = !strcmp(id, "freezing-puffer") || !strcmp(id, "cold-layers");
    bool fog = !strcmp(id, "fog-overshirt");
    if (rain) {
        cloud(5 + (int)lroundf(sinf(t * 0.6f)), 24);
        cloud(74, 34);
        for (int i = 0; i < 8; i++) {
            int x = i & 1 ? 79 + (i % 3) * 4 : 6 + (i % 3) * 5;
            int y = 43 + (int)fmodf(t * 17 + i * 13, 39);
            rect(x, y, 1, 3, i & 1 ? ICE : BLUE);
        }
        int spread = (int)fmodf(t * 6, 4);
        rect(11 - spread, 87, 2, 1, BLUE);
        rect(13 + spread, 87, 2, 1, BLUE);
    } else if (snow) {
        for (int i = 0; i < 10; i++) {
            int x = (i & 1 ? 81 : 11) + (int)lroundf(sinf(t * 0.8f + i) * 5);
            int y = 19 + (int)fmodf(t * 6 + i * 13, 66);
            star(x, y, i % 3 ? 1 : 2, i & 1 ? ICE : LILAC);
        }
    } else if (wind || fog) {
        for (int i = 0; i < 6; i++) {
            int x = (i & 1 ? 76 : 4) + (int)fmodf(t * 7 + i * 3, 8);
            int y = 25 + i * 10 + (int)lroundf(sinf(t * 1.8f + i) * 2);
            rect(x, y, 9, 1, fog ? BLUE : LILAC);
            rect(x + 2, y + 2, 4, 1, fog ? SHADOW : LEAF);
        }
    } else {
        int x = 14, y = 26 + (int)lroundf(sinf(t * 0.9f));
        rect(x - 2, y - 2, 3, 2, PEACH);
        rect(x - 3, y - 4, 7, 9, GOLD);
        rect(x - 4, y - 3, 9, 7, GOLD);
        int ray = 8 + ((int)(t * 2) & 1);
        rect(x - 1, y - ray, 2, 2, GOLD);
        rect(x - 1, y + ray - 1, 2, 2, GOLD);
        rect(x - ray, y, 2, 1, GOLD);
        rect(x + ray - 1, y, 2, 1, GOLD);
        /* Two wing poses on a figure-eight path beside the character. */
        x = 82 + (int)lroundf(sinf(t * 1.4f) * 5);
        y = 47 + (int)lroundf(sinf(t * 2.8f) * 8);
        int wing = ((int)(t * 6) & 1) ? 4 : 2;
        rect(x - wing - 1, y - 2, wing, 3, PINK);
        rect(x + 1, y - 2, wing, 3, LILAC);
        rect(x - wing, y + 1, wing, 2, LILAC);
        rect(x + 1, y + 1, wing, 2, PINK);
        rect(x, y - 2, 1, 5, GOLD);
        rect(x - 1, y - 3, 1, 1, GOLD);
        rect(x + 1, y - 3, 1, 1, GOLD);
        for (int i = 0; i < 3; i++) {
            int radius = 1 + (sinf(t * 2.1f + i * 2) > 0.6f);
            star(i == 1 ? 83 : 10 + i * 2, 53 + i * 13, radius, i == 1 ? PINK : GOLD);
        }
    }
    /* Brief landing dust after each hop; it spreads and vanishes. */
    float phase = fmodf(t, 12.0f);
    if (phase >= 1.75f && phase < 2.25f) {
        int spread = 2 + (int)((phase - 1.75f) * 12);
        rect(31 - spread, 89, 3, 1, LILAC);
        rect(62 + spread, 89, 3, 1, LILAC);
    }
    if (happy > 0.2f) {
        int y = 64 - (int)fmodf(t * 10, 14);
        for (int x = 10; x <= 82; x += 72) {
            rect(x - 2, y, 2, 2, PINK); rect(x + 1, y, 2, 2, PINK);
            rect(x - 2, y + 2, 5, 1, PINK); rect(x - 1, y + 3, 3, 1, PINK);
            rect(x, y + 4, 1, 1, PINK);
        }
    }
}

static int find_id(const char *id)
{
    if (!id) return -1;
    if (!strcmp(id, "default")) return 0;
    for (unsigned i = 0; i < MUSE_WARDROBE_ASSET_COUNT; ++i) {
        if (!strcmp(id, muse_wardrobe_assets[i].id)) return (int)i + 1;
    }
    return -1;
}

bool muse_wardrobe_valid(const char *id)
{
    return find_id(id) >= 0;
}

bool muse_wardrobe_select(const char *id)
{
    int index = find_id(id);
    if (index < 0) return false;
    atomic_store(&s_selected, (unsigned)index);
    return true;
}

const char *muse_wardrobe_current(void)
{
    unsigned index = atomic_load(&s_selected);
    return index ? muse_wardrobe_assets[index - 1].id : "default";
}

static float unit(float value)
{
    return !isfinite(value) || value < 0 ? 0 : value > 1 ? 1 : value;
}

static bool arm_pixel(const happy_rig_t *rig, int x, int y, int width, int height)
{
    if (x < 0 || x >= width || y < rig->shoulder || y > rig->arm_end || y >= height) return false;
    int cut = rig->arm_cuts ? rig->arm_cuts[y - rig->shoulder]
        : rig->arm_width - (y - rig->shoulder) / 4 * rig->arm_taper;
    return x < cut || x >= width - cut;
}

static int rounded_fixed(int value)
{
    return (value + (value >= 0 ? 128 : -128)) / 256;
}

/* Interpolate skin between clean pixels bordering each facial mark. Copying
 * one color into each half of a row left a hard seam in the face's shading. */
static uint16_t face_fill(const muse_wardrobe_asset_t *asset, int x0, int x1, int x, int y)
{
    uint16_t a = asset->pixels[y * asset->width + x0 - 1];
    uint16_t b = asset->pixels[y * asset->width + x1 + 1];
    unsigned span = (unsigned)(x1 - x0 + 2), right = (unsigned)(x - x0 + 1), left = span - right;
    unsigned r = ((a >> 11) * left + (b >> 11) * right + span / 2) / span;
    unsigned g = (((a >> 5) & 63) * left + ((b >> 5) & 63) * right + span / 2) / span;
    unsigned blue = ((a & 31) * left + (b & 31) * right + span / 2) / span;
    return (uint16_t)((r << 11) | (g << 5) | blue);
}

/* Sample the small animated rig in source coordinates. Scaling caches repeated
 * rows, so this is not a full-screen framebuffer or per-display-pixel trig. */
static uint16_t happy_pixel(const muse_wardrobe_asset_t *asset,
                            const happy_rig_t *rig, int x, int y)
{
    for (int side = 0; side < 2; side++) {
        int pivot = side ? asset->width - 11 : 10;
        int dx = x - pivot, dy = y - rig->shoulder;
        int sine = side ? -s_arm_sin : s_arm_sin;
        int sx = pivot + rounded_fixed(dx * s_arm_cos + dy * sine);
        int sy = rig->shoulder + rounded_fixed(-dx * sine + dy * s_arm_cos);
        if (arm_pixel(rig, sx, sy, asset->width, asset->height)
            && (side ? sx >= asset->width / 2 : sx < asset->width / 2)) {
            uint16_t pixel = asset->pixels[sy * asset->width + sx];
            if (pixel) return pixel;
        }
    }
    if (x < 0 || x >= asset->width || y < 0 || y >= asset->height
        || arm_pixel(rig, x, y, asset->width, asset->height)) return 0;

    /* Replace only the anchored marks; the surrounding fur stays untouched. */
    int x0 = -1, x1 = -1, y0 = rig->ey0, y1 = rig->ey1;
    bool eye = y >= y0 && y <= y1;
    if (eye && x >= rig->lx0 && x <= rig->lx1) { x0 = rig->lx0; x1 = rig->lx1; }
    else if (eye && x >= rig->rx0 && x <= rig->rx1) { x0 = rig->rx0; x1 = rig->rx1; }
    else {
        eye = false;
        y0 = rig->my0; y1 = rig->my1;
        if (x >= rig->mx0 && x <= rig->mx1 && y >= y0 && y <= y1) {
            x0 = rig->mx0; x1 = rig->mx1;
        }
    }
    if (x0 > 0 && x1 + 1 < asset->width) {
        int u = x - x0, v = y - y0, width = x1 - x0 + 1;
        if (eye) {
            int arch = (u == 0 || u == width - 1) ? 2 : 1;
            if (v == arch) return RGB565(56, 35, 24);
        } else {
            if ((v == 0 && (u == 0 || u == width - 1))
                || (v == 1 && u > 0 && u < width - 1)
                || (v == 2 && u > 1 && u < width - 2)) return RGB565(56, 35, 24);
            if (v == 2 && (u == 1 || u == width - 2)) return PINK;
        }
        return face_fill(asset, x0, x1, x, y);
    }
    return asset->pixels[y * asset->width + x];
}

bool muse_wardrobe_render(const muse_pose_t *pose, bool ambient)
{
    s_effect_count = 0;
    s_excited = false;
    unsigned index = atomic_load(&s_selected);
    if (!index) {
        atomic_store(&s_frame, 0);
        return false;
    }
    const muse_wardrobe_asset_t *asset = &muse_wardrobe_assets[index - 1];
    float t = pose && isfinite(pose->t) ? fmodf(pose->t, 3600.0f) : 0;
    float level = pose ? unit(pose->level) : 0;
    float happy = pose ? unit(pose->happy) : 0;
    muse_mode_t mode = pose ? pose->mode : MUSE_MODE_IDLE;
    ambient = ambient && mode == MUSE_MODE_IDLE;
    s_excited = ambient && happy > 0.2f;
    if (s_excited) {
        float angle = 1.57079633f + sinf(t * 14.0f) * 0.22f * happy;
        s_arm_cos = (int)lroundf(cosf(angle) * 256);
        s_arm_sin = (int)lroundf(sinf(angle) * 256);
    }
    int width, height, left, bottom;
    if (ambient) {
        float phase = fmodf(t, 12.0f), jump = 0;
        if (phase < 0.9f) jump = sinf(phase / 0.9f * 3.14159265f) * 7;
        else if (phase >= 1.15f && phase < 1.75f)
            jump = sinf((phase - 1.15f) / 0.6f * 3.14159265f) * 4;
        if (happy > 0) jump = fabsf(sinf(t * 9.0f)) * 8.0f * happy;
        int hop = (int)lroundf(jump);
        /* Keep the artwork's proportions; motion comes from a real hop. */
        width = asset->width;
        height = asset->height;
        left = (MUSE_WARDROBE_GRID - width) / 2 + (int)lroundf(sinf(t * 0.7f) * 1.5f);
        bottom = 91 - hop;
        scenery(asset->id, t, hop, happy);
    } else {
        float rate = 1.6f, sway = 0.45f, energy = 0;
        switch (mode) {
        case MUSE_MODE_BOOT: rate = 2.0f; break;
        case MUSE_MODE_LISTENING: rate = 2.2f; energy = level; break;
        case MUSE_MODE_THINKING: rate = 2.6f; sway = 1.0f; break;
        case MUSE_MODE_SPEAKING: rate = 3.0f; energy = level * 1.5f; break;
        case MUSE_MODE_ERROR: rate = 4.0f; sway = 1.1f; break;
        case MUSE_MODE_OFF: rate = 0.8f; sway = 0; break;
        default: break;
        }
        float breath = sinf(t * rate);
        width = asset->width + (int)lroundf(breath * 0.6f);
        height = asset->height + (int)lroundf(breath + energy);
        left = (MUSE_WARDROBE_GRID - width) / 2 + (int)lroundf(sinf(t * 0.9f) * sway);
        bottom = 91 - (int)lroundf(sinf(t * rate * 0.5f))
                    - (int)lroundf(fabsf(sinf(t * 5.0f)) * happy * 3.0f);
    }
    int top = bottom - height;
    unsigned frame = index | ((unsigned)left << 4) | ((unsigned)top << 11)
                           | ((unsigned)width << 18) | ((unsigned)height << 25);
    atomic_store(&s_frame, frame);
    return true;
}

bool muse_wardrobe_scale(uint16_t *dst, int stride_px, int x0, int x1,
                         int y0, int y1, int canvas_px)
{
    unsigned frame = atomic_load(&s_frame);
    unsigned index = frame & 15u;
    int64_t cols = (int64_t)x1 - x0 + 1;
    int64_t rows = (int64_t)y1 - y0 + 1;
    if (!index || !dst || canvas_px <= 0 || canvas_px > 512
        || cols <= 0 || cols > 512 || rows <= 0 || rows > 512
        || stride_px < cols || stride_px <= 0) return false;

    const muse_wardrobe_asset_t *asset = &muse_wardrobe_assets[index - 1];
    int left = (frame >> 4) & 127u, top = (frame >> 11) & 127u;
    int width = (frame >> 18) & 127u, height = (frame >> 25) & 127u;
    /* Divisions happen once per column/row, not for each destination pixel.
     * Validated coordinates fit ordinary 32-bit math on the ESP32. */
    int8_t xmap[512];
    for (int col = 0; col < (int)cols; ++col) {
        int64_t x = (int64_t)x0 + col;
        xmap[col] = INT8_MIN;
        if (x >= 0 && x < canvas_px) {
            int gx = (int)x * MUSE_WARDROBE_GRID / canvas_px;
            if (s_excited || (gx >= left && gx < left + width)) {
                xmap[col] = (int8_t)((gx - left) * asset->width / width);
            }
        }
    }
    uint16_t *previous = NULL;
    uint16_t *output = dst;
    int previous_sy = -2;
    for (int row = 0; row < (int)rows; ++row) {
        int64_t y = (int64_t)y0 + row;
        int gy = y >= 0 && y < canvas_px ? (int)y * MUSE_WARDROBE_GRID / canvas_px : -1;
        int sy = gy >= 0 && (s_excited || (gy >= top && gy < top + height))
                     ? (gy - top) * asset->height / height : INT16_MIN;
        if (previous && previous_sy == sy) {
            memcpy(dst, previous, (size_t)cols * sizeof(*dst));
        } else if (sy == INT16_MIN) {
            memset(dst, 0, (size_t)cols * sizeof(*dst));
        } else if (s_excited) {
            const happy_rig_t *rig = &s_rigs[index - 1];
            for (int col = 0; col < (int)cols; ++col)
                dst[col] = xmap[col] == INT8_MIN ? 0 : happy_pixel(asset, rig, xmap[col], sy);
        } else {
            const uint16_t *source = asset->pixels + sy * asset->width;
            for (int col = 0; col < (int)cols; ++col) {
                dst[col] = xmap[col] == INT8_MIN ? 0 : source[xmap[col]];
            }
        }
        previous = dst;
        previous_sy = sy;
        dst += stride_px;
    }
    /* Overlay only the black background, after row reuse. Scenery rows need
     * not match the sprite source row (especially its empty top margin). */
    for (unsigned i = 0; i < s_effect_count; i++) {
        pixel_rect_t r = s_effects[i];
        int rx0 = (r.x * canvas_px + MUSE_WARDROBE_GRID - 1) / MUSE_WARDROBE_GRID;
        int ry0 = (r.y * canvas_px + MUSE_WARDROBE_GRID - 1) / MUSE_WARDROBE_GRID;
        int rx1 = ((r.x + r.w) * canvas_px + MUSE_WARDROBE_GRID - 1) / MUSE_WARDROBE_GRID - 1;
        int ry1 = ((r.y + r.h) * canvas_px + MUSE_WARDROBE_GRID - 1) / MUSE_WARDROBE_GRID - 1;
        if (rx0 < 0) rx0 = 0;
        if (ry0 < 0) ry0 = 0;
        if (rx1 >= canvas_px) rx1 = canvas_px - 1;
        if (ry1 >= canvas_px) ry1 = canvas_px - 1;
        if (rx0 < x0) rx0 = x0;
        if (ry0 < y0) ry0 = y0;
        if (rx1 > x1) rx1 = x1;
        if (ry1 > y1) ry1 = y1;
        for (int y = ry0; y <= ry1; y++) {
            uint16_t *row = output + (y - y0) * stride_px;
            for (int x = rx0; x <= rx1; x++)
                if (!row[x - x0]) row[x - x0] = r.color;
        }
    }
    return true;
}
