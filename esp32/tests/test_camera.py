#!/usr/bin/env python3
# Modified by contains-studio for Muse Watcher (2026); see root CHANGES.md.
# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""components/camera: the registry's one-holder rule for frames and streams and
backend start-up (camera_harness.c, against a scripted backend), and the SSCMA
transport's packets and reply framing (sscma_proto_harness.c)."""

from __future__ import annotations

import base64
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CAMERA = ROOT / "components" / "camera"

# An 8x8 red baseline JPEG generated locally with Pillow, with no camera data.
JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAYEBQYFBAYGBQYHBwYIChAKCgkJChQODwwQFxQYGBcUFhYaHSUfGhsjHBYWICwgIyYn"
    "KSopGR8tMC0oMCUoKSj/2wBDAQcHBwoIChMKChMoGhYaKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgo"
    "KCgoKCgoKCj/wAARCAAIAAgDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUF"
    "BAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVW"
    "V1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi"
    "4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAEC"
    "AxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVm"
    "Z2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq"
    "8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDzqiiivjj+kT//2Q=="
)

# The bits of ESP-IDF the component uses.
ESP_ERR = """#pragma once
typedef int esp_err_t;
#define ESP_OK 0
#define ESP_FAIL -1
#define ESP_ERR_INVALID_STATE 0x103
#define ESP_ERR_NOT_SUPPORTED 0x106
#define ESP_ERR_TIMEOUT 0x107
"""


class CameraTest(unittest.TestCase):
    def run_harness(self) -> bytes:
        cc = shlex.split(os.environ.get("CC", "cc"))
        if not cc or shutil.which(cc[0]) is None:
            self.skipTest("C compiler not available")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            (out / "esp_err.h").write_text(ESP_ERR)
            binary = out / "camera_harness"
            proc = subprocess.run([*cc, "-std=c11", "-Wall", "-Wextra", "-Werror", "-I", str(out),
                                   "-I", str(CAMERA), str(ROOT / "tests" / "camera_harness.c"),
                                   str(CAMERA / "camera.c"), "-o", str(binary)],
                                  text=True, capture_output=True)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            proc = subprocess.run([str(binary)], capture_output=True)
            self.assertEqual(proc.returncode, 0, proc.stderr.decode())
            return proc.stdout

    def test_sscma_packets_and_replies(self) -> None:
        cc = shlex.split(os.environ.get("CC", "cc"))
        if not cc or shutil.which(cc[0]) is None:
            self.skipTest("C compiler not available")
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / "sscma_proto_harness"
            proc = subprocess.run([*cc, "-std=c11", "-Wall", "-Wextra", "-Werror", "-fsanitize=address,undefined",
                                   "-I", str(CAMERA), str(ROOT / "tests" / "sscma_proto_harness.c"),
                                   str(CAMERA / "sscma_proto.c"), "-o", str(binary)],
                                  text=True, capture_output=True)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            proc = subprocess.run([str(binary)], capture_output=True)
            self.assertEqual((proc.returncode, proc.stdout), (0, b"ok\n"), proc.stderr.decode())

    def test_registry_frames_and_streams(self) -> None:
        self.assertEqual(self.run_harness(), b"ok\n")


class CameraJpegFramingTest(unittest.TestCase):
    """Run the real SSCMA backend framing helper, without SPI, JSON or a decoder."""
    @classmethod
    def setUpClass(cls):
        cc = shlex.split(os.environ.get("CC", "cc"))
        if not cc or shutil.which(cc[0]) is None:
            raise unittest.SkipTest("C compiler not available")
        source = (CAMERA / "camera_sscma.c").read_text()
        match = re.search(r"static size_t jpeg_frame_length\([^;]*?\)\n\{", source)
        if not match:
            raise AssertionError("camera backend has no JPEG boundary normalization")
        start = source.index("{", match.start())
        end, depth = start + 1, 1
        while depth:
            depth += (source[end] == "{") - (source[end] == "}")
            end += 1
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        directory = Path(cls.tmp.name)
        harness = directory / "jpeg.c"
        harness.write_text("#include <stdbool.h>\n#include <stdint.h>\n#include <stddef.h>\n#include <stdio.h>\n"
            + source[match.start():end] + r'''
int main(void) {
    uint8_t data[65536];
    size_t n = fread(data, 1, sizeof(data), stdin);
    printf("%zu\n", jpeg_frame_length(data, n));
    return 0;
}
''')
        cls.binary = directory / "jpeg"
        proc = subprocess.run([*cc, "-std=c11", "-Wall", "-Wextra", "-Werror",
            "-fsanitize=address,undefined", str(harness), "-o", str(cls.binary)], capture_output=True, text=True)
        if proc.returncode:
            raise AssertionError(proc.stdout + proc.stderr)

    def frame_length(self, jpeg):
        proc = subprocess.run([str(self.binary)], input=jpeg, capture_output=True)
        self.assertEqual(proc.returncode, 0, proc.stderr.decode())
        return int(proc.stdout)

    @staticmethod
    def with_app(payload):
        return JPEG[:2] + b"\xff\xe1" + (len(payload) + 2).to_bytes(2, "big") + payload + JPEG[2:]

    def test_real_jpeg_unchanged_and_observed_padded_frame_lengths(self):
        self.assertEqual(self.frame_length(JPEG), len(JPEG))
        for eoi, total in ((4060, 4096), (4063, 4100)):
            jpeg = self.with_app(b"x" * (eoi + 2 - len(JPEG) - 4))
            self.assertEqual(len(jpeg), eoi + 2)
            # Suffix bytes are deliberately arbitrary: hardware diagnostics only
            # established the length, not that the padding contains zeros.
            suffix = bytes(range(total - len(jpeg)))
            self.assertEqual(self.frame_length(jpeg + suffix), len(jpeg))

    def test_marker_inside_app_and_after_image_do_not_change_boundary(self):
        jpeg = self.with_app(b"EXIF\x00\xff\xd9\xff\xda\xff\xd8metadata")
        self.assertEqual(self.frame_length(jpeg + b"pad\xff\xd9tail"), len(jpeg))

    def test_entropy_stuffing_restart_fill_and_multiple_scans(self):
        sos = JPEG.index(b"\xff\xda")
        header_end = sos + 2 + int.from_bytes(JPEG[sos + 2:sos + 4], "big")
        header, scan = JPEG[:header_end], JPEG[sos:header_end]
        # Synthetic entropy syntax, not a decodable image: exercise FF00 byte
        # stuffing, restart markers/fill, TEM, DNL and an intervening COM segment.
        entropy = b"\x11\xff\x00\xd9\x22\xff\xd0\x33\xff\xff\xd7\x44\xff\x01\x55"
        for body in (entropy, entropy + b"\xff\xdc\x00\x04\x00\x08\x66",
                     entropy + b"\xff\xfe\x00\x06a\xff\xd9b" + scan + b"\x77"):
            jpeg = header + body + b"\xff\xd9"
            self.assertEqual(self.frame_length(jpeg + b"\x00\xff\xd9junk"), len(jpeg))

    def test_rejects_truncated_or_malformed_marker_structure(self):
        for end in (0, 1, 2, 3, 4, 5, 20, 100, 300, len(JPEG) - 2, len(JPEG) - 1):
            with self.subTest(end=end):
                self.assertEqual(self.frame_length(JPEG[:end]), 0)
        for jpeg in (b"\xff\xd8\xff\xd9", b"\xff\xd8\xff\xe1\x00\x01\xff\xd9",
                     b"\xff\xd8\xff\xe1\xff\xff\xff\xd9", b"\xff\xd8\xff\xd8",
                     b"\xff\xd8\xff\x00\xff\xd9", JPEG[:-2] + b"\x11\x22\xff"):
            with self.subTest(jpeg=jpeg[:12]):
                self.assertEqual(self.frame_length(jpeg), 0)


if __name__ == "__main__":
    unittest.main()
