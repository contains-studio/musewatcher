"""Run the native weather command and registration against real cJSON."""

from pathlib import Path
import json
import os
import shlex
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
JSON = Path(os.environ.get(
    "CJSON_SOURCE_DIR", ROOT / "managed_components/espressif__cjson/cJSON"
))


class MuseWeatherCommandTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.out = Path(cls.temp.name)
        (cls.out / "esp_err.h").write_text(
            "#pragma once\ntypedef int esp_err_t;\n"
            "#define ESP_OK 0\n#define ESP_ERR_INVALID_STATE 2\n"
        )
        source = cls.out / "weather.c"
        source.write_text(r'''
#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "muse_weather_command.h"
#include "muse_ui.h"
#include "muse_wardrobe.h"
static int calls, show_error;
static muse_weather_card_t shown;
bool muse_wardrobe_valid(const char *id) {
    return id && (!strcmp(id, "default") || !strcmp(id, "warm-crochet"));
}
esp_err_t muse_ui_weather_show(const muse_weather_card_t *card) {
    calls++;
    shown = *card;
    return show_error;
}
int main(int argc, char **argv) {
    assert(argc >= 2);
    cJSON *params = cJSON_Parse(argv[1]);
    if (argc > 2) show_error = atoi(argv[2]);
    if (argc > 3) {
        cJSON *item = cJSON_GetObjectItemCaseSensitive(params, argv[3]);
        assert(item);
        item->valuedouble = NAN;
    }
    cJSON *result = muse_weather_command(params);
    assert(result);
    cJSON_AddNumberToObject(result, "test_ui_calls", calls);
    if (calls) {
        cJSON *card = cJSON_AddObjectToObject(result, "test_card");
        cJSON_AddStringToObject(card, "outfit_id", shown.outfit_id);
        cJSON_AddNumberToObject(card, "present", shown.present);
        cJSON_AddNumberToObject(card, "temp_f", shown.temp_f);
        cJSON_AddNumberToObject(card, "high_f", shown.high_f);
        cJSON_AddNumberToObject(card, "low_f", shown.low_f);
        cJSON_AddNumberToObject(card, "wind_mph", shown.wind_mph);
        cJSON_AddNumberToObject(card, "humidity_pct", shown.humidity_pct);
    }
    cJSON_Delete(params); /* UI receives owned values, not borrowed strings. */
    char *text = cJSON_PrintUnformatted(result);
    assert(text);
    puts(text);
    free(text);
    cJSON_Delete(result);
    return 0;
}
''')
        cls.compiler = shlex.split(os.environ.get("CC", "cc"))
        cls.flags = ["-std=c11", "-Wall", "-Wextra", "-Werror",
                     "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
                     "-I", str(cls.out), "-I", str(JSON),
                     "-I", str(ROOT / "main"),
                     "-I", str(ROOT / "components/muse")]
        # New macOS SDKs deprecate sprintf inside the unmodified cJSON source.
        compiled = subprocess.run(
            [*cls.compiler, *cls.flags, "-Wno-deprecated-declarations", "-c",
             str(JSON / "cJSON.c"), "-o", str(cls.out / "cjson.o")],
            capture_output=True, text=True,
        )
        if compiled.returncode:
            raise AssertionError(compiled.stdout + compiled.stderr)
        cls.build([source, ROOT / "main/muse_weather_command.c"], "weather")

    @classmethod
    def build(cls, sources, name):
        result = subprocess.run(
            [*cls.compiler, *cls.flags, *map(str, sources), str(cls.out / "cjson.o"),
             "-lm", "-o", str(cls.out / name)],
            capture_output=True, text=True,
        )
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    def command(self, payload, error=0, nan_field=None):
        args = [str(self.out / "weather"), json.dumps(payload), str(error)]
        if nan_field:
            args.append(nan_field)
        run = subprocess.run(args, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        return json.loads(run.stdout)

    def test_required_only_and_complete_card_copy(self):
        for payload, present in (
            ({"temp_f": 75, "outfit_id": "warm-crochet"}, 0),
            ({"temp_f": -12.5, "outfit_id": "default", "high_f": 2,
              "low_f": -20, "wind_mph": 10.25, "humidity_pct": 52}, 15),
        ):
            result = self.command(payload)
            self.assertTrue(result["ok"])
            self.assertTrue(result["accepted"])
            self.assertEqual(result["test_ui_calls"], 1)
            self.assertEqual(result["outfit_id"], payload["outfit_id"])
            self.assertEqual(result["test_card"]["present"], present)
            for key, value in payload.items():
                self.assertEqual(result["test_card"][key], value)

    def test_optional_presence_and_inclusive_boundaries(self):
        for field, minimum, maximum, bit in (
            ("temp_f", -238, 302, 0), ("high_f", -238, 302, 1),
            ("low_f", -238, 302, 2), ("wind_mph", 0, 400, 4),
            ("humidity_pct", 0, 100, 8),
        ):
            for value in (minimum, maximum):
                with self.subTest(field=field, value=value):
                    result = self.command({"temp_f": 75, "outfit_id": "default", field: value})
                    self.assertTrue(result["ok"])
                    self.assertEqual(result["test_card"]["present"], bit)
                    self.assertEqual(result["test_card"][field], value)

    def test_invalid_payloads_never_change_outfit_or_card(self):
        invalid = [None, [], "weather", {}, {"temp_f": 75},
                   {"outfit_id": "default"}, {"temp_f": 75, "outfit_id": "unsupported"},
                   {"temp_f": 75, "outfit_id": "x" * 32},
                   {"temp_f": 75, "outfit_id": None},
                   {"temp_f": 75, "outfit_id": ""},
                   {"temp_f": 75, "outfit_id": 1},
                   {"temp_f": 75, "outfit_id": "default", "high_f": 60, "low_f": 61},
                   {"temp_f": 75, "outfit_id": "default", "high_f": 60, "low_f": 60.0000001}]
        for field, minimum, maximum in (
            ("temp_f", -238, 302), ("high_f", -238, 302), ("low_f", -238, 302),
            ("wind_mph", 0, 400), ("humidity_pct", 0, 100),
        ):
            for value in (None, True, False, "75", [], {}, minimum - .1, maximum + .1,
                          float("inf"), -float("inf")):
                invalid.append({"temp_f": 75, "outfit_id": "default", field: value})
        for payload in invalid:
            with self.subTest(payload=payload):
                result = self.command(payload)
                self.assertFalse(result["ok"])
                self.assertEqual(result["error"]["code"], "invalid_params")
                self.assertEqual(result["test_ui_calls"], 0)
        for field in ("temp_f", "high_f", "low_f", "wind_mph", "humidity_pct"):
            with self.subTest(nonfinite=field):
                result = self.command({"temp_f": 75, "outfit_id": "default", field: 1},
                                      nan_field=field)
                self.assertFalse(result["ok"])
                self.assertEqual(result["test_ui_calls"], 0)

    def test_ui_readiness_and_persistence_errors_are_not_success(self):
        for error, code in ((2, "not_ready"), (3, "storage_error")):
            result = self.command({"temp_f": 75, "outfit_id": "default"}, error=error)
            self.assertFalse(result["ok"])
            self.assertEqual(result["error"]["code"], code)
            self.assertEqual(result["test_ui_calls"], 1)
            self.assertNotIn("accepted", result)

    def test_watcher_registration_fits_and_describes_required_weather_fields(self):
        noise = (ROOT / "main/noise_control.cpp").read_text()
        start = noise.index("static cJSON *string_param(")
        builders = noise[start:noise.index("// ---- OTA callback", start)]
        source = self.out / "registration.c"
        source.write_text('''
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cJSON.h"
#define nullptr NULL
#define CONFIG_HOMEHUB_TUNNEL 1
#define CONFIG_MUSE_BOARD_SENSECAP_WATCHER 1
#define CONFIG_HOMEHUB_DISPLAY_COMMANDS 1
#define CONFIG_HOMEHUB_LED_BACKEND_MUSE 1
#define CONFIG_MUSE_WATCHER_CAMERA 1
typedef struct { const char *version; } esp_app_desc_t;
static bool ota_is_enabled(void) { return true; }
static const esp_app_desc_t *esp_app_get_description(void) {
    static const esp_app_desc_t desc = {"999.0.0"}; return &desc;
}
static bool led_status_display_info(int *w, int *h) { *w = *h = 412; return true; }
static int led_status_display_bits(void) { return 16; }
static char s_wifi_ssid[33] = "12345678901234567890123456789012", s_register_req_id[40];
static const char *s_node_id = "test-node", *s_display_name = "Test device";
static void copy_wifi_ssid(char *out, size_t size) { snprintf(out, size, "%s", s_wifi_ssid); }
static void make_uuid(char *out, size_t size) { snprintf(out, size, "test-request"); }
''' + builders + '''
int main(void) {
    char *json = build_register_json();
    assert(json);
    assert(strlen(json) < 8192 - 5);
    puts(json);
    free(json);
    return 0;
}
''')
        self.build([source], "registration")
        run = subprocess.run([str(self.out / "registration")], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        command = json.loads(run.stdout)["params"]["commands_v2"]["display.weather"]
        self.assertEqual(set(command["required"]), {"temp_f", "outfit_id"})
        self.assertEqual(set(command["optional"]), {"high_f", "low_f", "wind_mph", "humidity_pct"})
        self.assertEqual(command["required"]["temp_f"]["type"], "number")
        self.assertEqual(command["optional"]["humidity_pct"]["maximum"], 100)
        self.assertNotIn("url", command["required"])


if __name__ == "__main__":
    unittest.main()
