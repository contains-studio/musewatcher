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

"""Exercise bounded serial photo framing and strict firmware base64 decoding."""
import base64
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "muse"))
import photo


class PhotoTest(unittest.TestCase):
    def test_upload_lines_preserve_jpeg_and_fit_console(self):
        for size in (4, 479, 480, 481, photo.MAX_BYTES):
            jpeg = b"\xff\xd8" + bytes((i % 256 for i in range(size - 4))) + b"\xff\xd9"
            lines = list(photo.split_photo(jpeg))
            payload = b""
            for i, (line, total) in enumerate(lines):
                last = i == len(lines) - 1
                self.assertTrue(line.startswith(b">photo=" if last else b">photo+="))
                self.assertLess(len(line), 1024)
                payload += base64.b64decode(line.split(b"=", 1)[1], validate=False)
                self.assertEqual(len(payload), total)
                if not last:
                    self.assertNotIn(b"=", line.split(b"=", 1)[1])
            self.assertEqual(payload, jpeg)

    def test_invalid_file_rejected_before_usb(self):
        for jpeg in (b"", b"\xff\xd8", b"not a JPEG", b"\xff\xd8" + b"a" * photo.MAX_BYTES + b"\xff\xd9"):
            with self.assertRaises(photo.BoardError):
                list(photo.split_photo(jpeg))

    class Board:
        def __init__(self, events):
            self.events, self.writes = list(events), []
        def drain(self, _seconds): pass
        def wake(self): pass
        def write_line(self, line): self.writes.append(line)
        def write(self, line): self.writes.append(line)
        def frame(self, _deadline, kind):
            assert kind == "@photo"
            return self.events.pop(0) if self.events else None

    def test_success_requires_server_ack(self):
        board = self.Board([{"type": "cancelled"}, {"type": "ack", "bytes": 4},
                            {"type": "accepted"}, {"type": "sent"}])
        photo.send_photo(board, b"\xff\xd8\xff\xd9")
        self.assertEqual(len(board.writes), 2)
        self.assertFalse(board.events)

    def test_timeout_after_submit_never_resends(self):
        board = self.Board([{"type": "cancelled"}, {"type": "ack", "bytes": 4}, {"type": "accepted"}])
        with self.assertRaises(photo.BoardError): photo.send_photo(board, b"\xff\xd8\xff\xd9")
        self.assertEqual(len(board.writes), 2)

    def test_busy_and_transfer_corruption_stop(self):
        for events in ([{"type": "error", "code": "ALREADY_SUBMITTED"}],
                       [{"type": "cancelled"}, {"type": "ack", "bytes": 3}]):
            board = self.Board(events)
            with self.assertRaises(photo.BoardError): photo.send_photo(board, b"\xff\xd8\xff\xd9")
            self.assertLessEqual(len(board.writes), 2)


class DecoderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cc = shlex.split(os.environ.get("CC", "cc"))
        if not cc or not shutil.which(cc[0]): raise unittest.SkipTest("C compiler unavailable")
        cls.tmp = tempfile.TemporaryDirectory()
        source = Path(cls.tmp.name) / "decode.c"
        source.write_text('''#include <stdio.h>
#include <string.h>
#include "muse_serial_photo.h"
int main(int argc, char **argv) {
    unsigned char out[2048]; size_t n = 0;
    if (argc < 3 || !muse_serial_photo_decode(argv[2], strlen(argv[2]), argv[1][0] == '1', out, sizeof(out), &n)) return 2;
    return fwrite(out, 1, n, stdout) == n ? 0 : 3;
}
''')
        cls.binary = Path(cls.tmp.name) / "decode"
        subprocess.run([*cc, "-std=c11", "-Wall", "-Wextra", "-Werror", "-I", str(ROOT / "components" / "muse"), str(source), "-o", str(cls.binary)], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls): cls.tmp.cleanup()

    def decode(self, text, last=True):
        return subprocess.run([str(self.binary), str(int(last)), text], capture_output=True)

    def test_all_byte_values_and_padding(self):
        for size in (0, 1, 2, 3, 255, 256, 257, 1024):
            raw = bytes(i % 256 for i in range(size))
            result = self.decode(base64.b64encode(raw).decode())
            self.assertEqual((result.returncode, result.stdout), (0, raw))

    def test_rejects_malformed_or_noncanonical_base64(self):
        for text in ("A", "AA", "AAA", "====", "A===", "=AAA", "AA=A", "AA$A", "AA A", "AA\nA", "AB==", "AAB=", "AA==AAAA", "AA-_", "AAAA" * 700):
            self.assertNotEqual(self.decode(text).returncode, 0, text)
        for text in ("AA==", "AAA="):
            self.assertNotEqual(self.decode(text, last=False).returncode, 0)


if __name__ == "__main__": unittest.main()
