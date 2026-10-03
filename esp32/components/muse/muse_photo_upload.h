/* Copyright (c) Meta Platforms, Inc. and affiliates.
 * Licensed under the Apache License, Version 2.0. */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define MUSE_PHOTO_MAX_BYTES (192 * 1024)

static inline bool muse_photo_valid(const uint8_t *jpeg, size_t len)
{
    return jpeg && len >= 4 && len <= MUSE_PHOTO_MAX_BYTES
        && jpeg[0] == 0xff && jpeg[1] == 0xd8
        && jpeg[len - 2] == 0xff && jpeg[len - 1] == 0xd9;
}

typedef bool (*muse_photo_write_t)(const uint8_t *data, size_t len, bool last, void *ctx);
typedef size_t (*muse_photo_encode_t)(const uint8_t *data, size_t len, char *out);

/* A bounded streaming body: no full-size JSON/base64 copies. Image items are
 * the Muse web-client convention; acceptance is checked on the live server. */
static inline bool muse_photo_write_json(const uint8_t *jpeg, size_t len,
                                         char *scratch, size_t cap,
                                         muse_photo_encode_t encode,
                                         muse_photo_write_t write, void *ctx)
{
    static const char head[] =
        "{\"message\":\"Describe this photo from my Watcher.\",\"output_modality\":\"text\","
        "\"items\":[{\"type\":\"image\",\"mime_type\":\"image/jpeg\","
        "\"filename\":\"watcher.jpg\",\"data_base64\":\"";
    static const char tail[] = "\"}]}";
    if (!muse_photo_valid(jpeg, len) || !scratch || cap < 4 || !encode || !write) return false;
    if (!write((const uint8_t *)head, sizeof(head) - 1, false, ctx)) return false;
    size_t part = cap / 4 * 3;
    for (size_t off = 0; off < len; off += part) {
        size_t n = len - off < part ? len - off : part;
        size_t encoded = encode(jpeg + off, n, scratch);
        if (!write((const uint8_t *)scratch, encoded, false, ctx)) return false;
    }
    return write((const uint8_t *)tail, sizeof(tail) - 1, true, ctx);
}
