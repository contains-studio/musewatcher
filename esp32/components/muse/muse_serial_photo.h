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
#include <stddef.h>
#include <stdint.h>

#define MUSE_SERIAL_PHOTO_MAX (192u * 1024u)

static inline int muse_photo_b64_digit(unsigned char c)
{
    if (c >= 'A' && c <= 'Z') return c - 'A';
    if (c >= 'a' && c <= 'z') return c - 'a' + 26;
    if (c >= '0' && c <= '9') return c - '0' + 52;
    return c == '+' ? 62 : c == '/' ? 63 : -1;
}

/* Each console piece is a whole number of base64 quartets. Padding is legal
 * only at the end of the final piece; reject whitespace and noncanonical bits. */
static inline bool muse_serial_photo_decode(const char *text, size_t len, bool last,
                                           uint8_t *out, size_t cap, size_t *written)
{
    *written = 0;
    if (len % 4) return false;
    for (size_t i = 0; i < len; i += 4) {
        int a = muse_photo_b64_digit((unsigned char)text[i]);
        int b = muse_photo_b64_digit((unsigned char)text[i + 1]);
        int c = muse_photo_b64_digit((unsigned char)text[i + 2]);
        int d = muse_photo_b64_digit((unsigned char)text[i + 3]);
        bool pad_c = text[i + 2] == '=', pad_d = text[i + 3] == '=';
        if (a < 0 || b < 0 || (!pad_c && c < 0) || (!pad_d && d < 0)
            || (pad_c && !pad_d) || ((pad_c || pad_d) && (!last || i + 4 != len))
            || (pad_c && (b & 15)) || (!pad_c && pad_d && (c & 3))) return false;
        size_t n = pad_c ? 1 : pad_d ? 2 : 3;
        if (n > cap - *written) return false;
        out[(*written)++] = (uint8_t)((a << 2) | (b >> 4));
        if (n > 1) out[(*written)++] = (uint8_t)((b << 4) | (c >> 2));
        if (n > 2) out[(*written)++] = (uint8_t)((c << 6) | d);
    }
    return true;
}
