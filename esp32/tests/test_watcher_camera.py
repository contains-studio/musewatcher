#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
# Licensed under the Apache License, Version 2.0.

"""Exercise the real Watcher camera worker using deterministic camera/voice fakes.

JPEG decoding is represented by a pixel marker, so these tests check ownership,
review/send identity and queued actions, not the JPEG codec or real task timing.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class WatcherCameraTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cc = shlex.split(os.environ.get("CC", "cc"))
        if not cc or shutil.which(cc[0]) is None:
            raise unittest.SkipTest("C compiler not available")
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        temp = Path(cls.tmp.name)
        for header in (
            "esp_err.h", "esp_heap_caps.h", "esp_log.h", "esp_timer.h", "freertos/FreeRTOS.h",
            "freertos/idf_additions.h", "freertos/queue.h", "freertos/semphr.h",
            "freertos/task.h", "mbedtls/base64.h", "muse_board.h", "muse_state.h",
            "muse_ui.h", "muse_voice.h", "rom/tjpgd.h",
        ):
            path = temp / header
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('#include "watcher_camera_fakes.h"\n')
        cls.binary = temp / "watcher_camera_harness"
        proc = subprocess.run(
            [*cc, "-std=c11", "-Wall", "-Wextra", "-Werror",
             "-fsanitize=address,undefined", "-g", "-I", str(temp),
             "-I", str(ROOT / "tests"), "-I", str(ROOT / "components/camera"),
             "-include", str(ROOT / "tests/host_compat.h"),
             str(ROOT / "tests/watcher_camera_harness.c"), "-o", str(cls.binary)],
            capture_output=True, text=True,
        )
        if proc.returncode:
            raise AssertionError(proc.stdout + proc.stderr)

    def run_case(self, name: str) -> None:
        proc = subprocess.run([str(self.binary), name], capture_output=True, text=True)
        self.assertEqual((proc.returncode, proc.stdout), (0, "ok\n"), proc.stderr)

    def test_freeze_sends_only_the_displayed_jpeg_on_explicit_send(self) -> None:
        self.run_case("identity")

    def test_failure_retry_retains_photo_and_ignores_old_ack(self) -> None:
        self.run_case("retry")

    def test_memory_decode_and_empty_preview_failures(self) -> None:
        self.run_case("frame-errors")

    def test_retake_cancel_and_stale_stream_callbacks(self) -> None:
        self.run_case("cancel")

    def test_remote_capture_uses_review_and_respects_busy_worker(self) -> None:
        self.run_case("remote")

    def test_start_failure_and_inline_send_completion(self) -> None:
        self.run_case("inline")


if __name__ == "__main__":
    unittest.main()
