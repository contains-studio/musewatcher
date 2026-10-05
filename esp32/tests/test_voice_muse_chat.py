#!/usr/bin/env python3
"""Link the Voice PE's real Muse adapter without the full UI settings module."""
from pathlib import Path
import os
import shlex
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
MUSE = ROOT / "components/muse"


class VoiceMuseChatTest(unittest.TestCase):
    def test_voice_only_adapter_supplies_microphone_hooks(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            headers = {
                "esp_err.h": "typedef int esp_err_t;\n",
                "freertos/FreeRTOS.h": """#pragma once
typedef int portMUX_TYPE;
#define portMUX_INITIALIZER_UNLOCKED 0
#define portENTER_CRITICAL(p) ((void)(p))
#define portEXIT_CRITICAL(p) ((void)(p))
""",
                "app.h": """#include <stdbool.h>
#include <stddef.h>
bool app_hatch_vm_credentials(const char *, char *, size_t, char *, size_t, char **);
""",
                "config_store.h": """#include <stdbool.h>
#include <stddef.h>
bool config_get_str(const char *, char *, size_t);
bool config_is_provisioned(void);
""",
                "wifi_mgr.h": "#include <stdbool.h>\nbool wifi_mgr_is_connected(void);\n",
                "muse_link.h": "",
                "muse_state.h": "",
                "muse_wifi.h": "",
            }
            for name, text in headers.items():
                path = work / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
            # Copy unchanged source so only its platform dependencies are faked.
            for name in ("voice_muse_chat.c", "voice_muse_chat.h", "voice_board.h"):
                shutil.copy(ROOT / "main" / name, work / name)
            shutil.copy(MUSE / "muse_settings.h", work / "muse_settings.h")
            (work / "harness.c").write_text(r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <string.h>
#include "muse_settings.h"
static bool muted;
bool voice_board_muted(void) { return muted; }
bool config_get_str(const char *k, char *s, size_t n) { (void)k; (void)s; (void)n; return false; }
bool config_is_provisioned(void) { return false; }
bool wifi_mgr_is_connected(void) { return false; }
bool app_hatch_vm_credentials(const char *w, char *i, size_t c, char *n, size_t z, char **t) {
    (void)w; (void)i; (void)c; (void)n; (void)z; (void)t; return false;
}
size_t test_strlcpy(char *dst, const char *src, size_t cap) {
    size_t n = strlen(src);
    if (cap) { size_t copy = n < cap - 1 ? n : cap - 1; memcpy(dst, src, copy); dst[copy] = 0; }
    return n;
}
int main(void) {
    assert(muse_settings_mic_on());
    assert(muse_settings_mic_generation() == 0);
    muted = true;
    assert(!muse_settings_mic_on());
    muted = false;
    assert(muse_settings_mic_on());
    assert(muse_settings_mic_generation() == 0); /* no software mute setting */
    return 0;
}
''')
            (work / "compat.h").write_text(
                "#include <stddef.h>\nsize_t test_strlcpy(char *, const char *, size_t);\n"
                "#define strlcpy test_strlcpy\n")
            cc = shlex.split(os.environ.get("CC", "cc"))
            build = subprocess.run(cc + ["-std=c11", "-Wall", "-Wextra", "-Werror",
                           "-include", str(work / "compat.h"), "-I", str(work),
                           str(work / "voice_muse_chat.c"), str(work / "harness.c"),
                           "-o", str(work / "test")], capture_output=True, text=True)
            self.assertEqual(build.returncode, 0, build.stderr)
            subprocess.run([str(work / "test")], check=True)


if __name__ == "__main__":
    unittest.main()
