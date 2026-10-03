#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
# Licensed under the Apache License, Version 2.0.
"""Exercise the real wardrobe persistence wrapper with isolated host storage."""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
MUSE = ROOT / "components/muse"
# Fake renderer's accepted values. Catalog/rendering correctness is tested by
# the renderer; these exercise persistence without an external skill checkout.
OUTFITS = ("default", "mild-knit", "warm-crochet", "cool-suede", "cold-layers",
           "wind-shell", "rain-shell", "heatwave-linen", "freezing-puffer",
           "cold-rain-parka", "fog-overshirt", "warm-rain-shell", "hot-wind-stripes", "hot-shorts")

HEADERS = {
    "esp_err.h": """#pragma once
typedef int esp_err_t;
#define ESP_OK 0
#define ESP_FAIL -1
#define ESP_ERR_NO_MEM 1
#define ESP_ERR_INVALID_ARG 2
#define ESP_ERR_INVALID_STATE 3
#define ESP_ERR_NVS_NOT_FOUND 4
#define ESP_ERR_NVS_INVALID_LENGTH 5
""",
    "esp_log.h": '#define ESP_LOGW(tag, ...) ((void)(tag))\n',
    "freertos/FreeRTOS.h": '#pragma once\n#define portMAX_DELAY -1\n',
    "freertos/semphr.h": """#pragma once
#include <pthread.h>
typedef pthread_mutex_t *SemaphoreHandle_t;
SemaphoreHandle_t xSemaphoreCreateMutex(void);
int xSemaphoreTake(SemaphoreHandle_t lock, int wait);
int xSemaphoreGive(SemaphoreHandle_t lock);
""",
    "nvs.h": """#pragma once
#include <stddef.h>
#include "esp_err.h"
typedef unsigned nvs_handle_t;
#define NVS_READONLY 0
#define NVS_READWRITE 1
esp_err_t nvs_open(const char *name, int mode, nvs_handle_t *handle);
esp_err_t nvs_get_str(nvs_handle_t h, const char *key, char *out, size_t *size);
esp_err_t nvs_set_str(nvs_handle_t h, const char *key, const char *value);
esp_err_t nvs_commit(nvs_handle_t h);
void nvs_close(nvs_handle_t h);
""",
    "muse_wardrobe.h": """#pragma once
#include <stdbool.h>
bool muse_wardrobe_valid(const char *id);
bool muse_wardrobe_select(const char *id);
const char *muse_wardrobe_current(void);
""",
}

HARNESS = r'''
#include <assert.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "freertos/semphr.h"
#include "nvs.h"
#include "muse_wardrobe_settings.h"
#include "muse_wardrobe.h"

static const char *const catalog[] = { @CATALOG@ };
static _Atomic(const char *) current = "default";
static pthread_mutex_t mutex = PTHREAD_MUTEX_INITIALIZER;
static bool allocation_fail, ns_exists, key_exists, pending, require_saved;
static char stored[256], staged[256];
static int open_error, read_error, save_error, commit_error;
static int opens, writes, commits, closes;

SemaphoreHandle_t xSemaphoreCreateMutex(void) { return allocation_fail ? NULL : &mutex; }
int xSemaphoreTake(SemaphoreHandle_t lock, int wait) {
    assert(lock == &mutex && wait == -1);
    assert(pthread_mutex_lock(lock) == 0); return 1;
}
int xSemaphoreGive(SemaphoreHandle_t lock) { assert(pthread_mutex_unlock(lock) == 0); return 1; }

bool muse_wardrobe_valid(const char *id) {
    if (!id) return false;
    for (size_t i = 0; i < sizeof(catalog) / sizeof(*catalog); i++)
        if (!strcmp(id, catalog[i])) return true;
    return false;
}
bool muse_wardrobe_select(const char *id) {
    if (!muse_wardrobe_valid(id)) return false;
    if (require_saved) assert(key_exists && !pending && !strcmp(stored, id));
    for (size_t i = 0; i < sizeof(catalog) / sizeof(*catalog); i++)
        if (!strcmp(id, catalog[i])) atomic_store(&current, catalog[i]);
    return true;
}
const char *muse_wardrobe_current(void) { return atomic_load(&current); }

esp_err_t nvs_open(const char *name, int mode, nvs_handle_t *h) {
    assert(!strcmp(name, "muse_wardrobe")); opens++;
    if (open_error) return open_error;
    if (!ns_exists && mode == NVS_READONLY) return ESP_ERR_NVS_NOT_FOUND;
    ns_exists = true; *h = 1; return ESP_OK;
}
esp_err_t nvs_get_str(nvs_handle_t h, const char *key, char *out, size_t *size) {
    assert(h == 1 && !strcmp(key, "outfit"));
    if (read_error) return read_error;
    if (!key_exists) return ESP_ERR_NVS_NOT_FOUND;
    size_t need = strlen(stored) + 1;
    if (*size < need) { *size = need; return ESP_ERR_NVS_INVALID_LENGTH; }
    memcpy(out, stored, need); *size = need; return ESP_OK;
}
esp_err_t nvs_set_str(nvs_handle_t h, const char *key, const char *id) {
    assert(h == 1 && !strcmp(key, "outfit")); writes++;
    if (save_error) return save_error;
    assert(strlen(id) < sizeof(staged)); strcpy(staged, id); pending = true; return ESP_OK;
}
esp_err_t nvs_commit(nvs_handle_t h) {
    assert(h == 1 && pending); commits++;
    if (commit_error) return commit_error;
    strcpy(stored, staged); pending = false; key_exists = true; return ESP_OK;
}
void nvs_close(nvs_handle_t h) { assert(h == 1); pending = false; closes++; }

static void seed(const char *id) {
    assert(strlen(id) < sizeof(stored)); strcpy(stored, id); ns_exists = key_exists = true;
}
static void is_current(const char *id) { assert(!strcmp(muse_wardrobe_current(), id)); }
static void *switch_outfits(void *arg) {
    const char *id = arg;
    for (int i = 0; i < 100; i++) assert(muse_wardrobe_settings_set(id) == ESP_OK);
    return NULL;
}

int main(int argc, char **argv) {
    assert(argc == 2); const char *scenario = argv[1];
    if (!strcmp(scenario, "before_init")) {
        assert(muse_wardrobe_settings_set("mild-knit") == ESP_ERR_INVALID_STATE);
        assert(opens == 0); is_current("default"); return 0;
    }
    if (!strcmp(scenario, "restore")) seed("cold-rain-parka");
    if (!strcmp(scenario, "missing_key")) ns_exists = true;
    if (!strcmp(scenario, "invalid_saved")) seed("not-an-outfit");
    if (!strcmp(scenario, "long_saved")) { memset(stored, 'x', 80); stored[80] = 0; ns_exists = key_exists = true; }
    if (!strcmp(scenario, "read_error")) { seed("mild-knit"); read_error = ESP_FAIL; }
    if (!strcmp(scenario, "open_error")) open_error = ESP_FAIL;
    if (!strcmp(scenario, "allocation_error")) allocation_fail = true;
    esp_err_t init = muse_wardrobe_settings_init();
    if (allocation_fail) { assert(init == ESP_ERR_NO_MEM && opens == 0); is_current("default"); return 0; }
    if (open_error || read_error) { assert(init == ESP_FAIL); is_current("default"); return 0; }
    assert(init == ESP_OK);
    if (!strcmp(scenario, "restore")) { is_current("cold-rain-parka"); assert(writes == 0); return 0; }
    is_current("default"); assert(writes == 0);
    if (!strcmp(scenario, "missing_namespace") || !strcmp(scenario, "missing_key") ||
        !strcmp(scenario, "invalid_saved") || !strcmp(scenario, "long_saved")) return 0;

    if (!strcmp(scenario, "invalid_set")) {
        int before = opens;
        assert(muse_wardrobe_settings_set(NULL) == ESP_ERR_INVALID_ARG);
        assert(muse_wardrobe_settings_set("") == ESP_ERR_INVALID_ARG);
        assert(muse_wardrobe_settings_set("unknown") == ESP_ERR_INVALID_ARG);
        assert(muse_wardrobe_settings_set("MILD-KNIT") == ESP_ERR_INVALID_ARG);
        assert(muse_wardrobe_settings_set("mild-knit\n") == ESP_ERR_INVALID_ARG);
        assert(opens == before && writes == 0); is_current("default"); return 0;
    }
    if (!strcmp(scenario, "unchanged")) {
        int before = opens;
        assert(muse_wardrobe_settings_set("default") == ESP_OK);
        assert(opens == before && writes == 0); return 0;
    }

    require_saved = true;
    assert(muse_wardrobe_settings_set("mild-knit") == ESP_OK);
    is_current("mild-knit"); assert(!strcmp(stored, "mild-knit"));
    assert(writes == 1 && commits == 1 && closes == 1);
    if (!strcmp(scenario, "persist")) {
        require_saved = false;
        assert(muse_wardrobe_select("default"));
        assert(muse_wardrobe_settings_init() == ESP_OK);
        is_current("mild-knit"); assert(writes == 1); return 0;
    }
    if (!strcmp(scenario, "unchanged_saved")) {
        int before = opens;
        assert(muse_wardrobe_settings_set("mild-knit") == ESP_OK);
        assert(opens == before && writes == 1); return 0;
    }
    if (!strcmp(scenario, "all_ids")) {
        for (size_t i = 0; i < sizeof(catalog) / sizeof(*catalog); i++) {
            assert(muse_wardrobe_settings_set(catalog[i]) == ESP_OK);
            is_current(catalog[i]); assert(!strcmp(stored, catalog[i]));
        }
        return 0;
    }
    if (!strcmp(scenario, "concurrent")) {
        pthread_t a, b;
        assert(!pthread_create(&a, NULL, switch_outfits, "hot-shorts"));
        assert(!pthread_create(&b, NULL, switch_outfits, "freezing-puffer"));
        assert(!pthread_join(a, NULL)); assert(!pthread_join(b, NULL));
        assert(!strcmp(stored, muse_wardrobe_current())); return 0;
    }
    if (!strcmp(scenario, "save_failure")) save_error = ESP_FAIL;
    else if (!strcmp(scenario, "commit_failure")) commit_error = ESP_FAIL;
    else if (!strcmp(scenario, "save_open_failure")) open_error = ESP_FAIL;
    else abort();
    assert(muse_wardrobe_settings_set("hot-shorts") == ESP_FAIL);
    is_current("mild-knit"); assert(!strcmp(stored, "mild-knit"));
    open_error = save_error = commit_error = 0;
    assert(muse_wardrobe_settings_set("hot-shorts") == ESP_OK);
    is_current("hot-shorts"); assert(!strcmp(stored, "hot-shorts"));
    return 0;
}
'''


class WardrobeSettingsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cc = shlex.split(os.environ.get("CC", "cc"))
        if not cc or not shutil.which(cc[0]):
            raise unittest.SkipTest("C compiler unavailable")
        cls.temp = tempfile.TemporaryDirectory()
        root = Path(cls.temp.name)
        for name, content in HEADERS.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        for name in ("muse_wardrobe_settings.c", "muse_wardrobe_settings.h"):
            shutil.copyfile(MUSE / name, root / name)
        (root / "harness.c").write_text(HARNESS.replace("@CATALOG@", ",".join(map(json.dumps, OUTFITS))))
        cls.binary = root / "wardrobe"
        result = subprocess.run([*cc, "-std=c11", "-Wall", "-Wextra", "-Werror", "-pthread",
            "-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-I", str(root),
            str(root / "harness.c"), str(root / "muse_wardrobe_settings.c"), "-o", str(cls.binary)],
            capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def scenario(self, name):
        result = subprocess.run([str(self.binary), name], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


for _scenario in ("before_init", "restore", "missing_namespace", "missing_key", "invalid_saved",
                  "long_saved", "read_error", "open_error", "allocation_error", "invalid_set",
                  "unchanged", "persist", "unchanged_saved", "all_ids", "concurrent",
                  "save_failure", "commit_failure", "save_open_failure"):
    setattr(WardrobeSettingsTest, "test_" + _scenario, lambda self, name=_scenario: self.scenario(name))


if __name__ == "__main__":
    unittest.main()
