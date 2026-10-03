#!/usr/bin/env python3
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

"""Send a local JPEG to Muse through a board on USB, without opening its camera.

    python3 tools/muse/photo.py --port PORT photo.jpg

The board uses the same photo upload path as its camera's Send photo button.
Success means Muse acknowledged the upload; view its reply in the Muse app.
"""
import argparse
import base64
from pathlib import Path
import sys
import time

from chat import Board, BoardError, pick_port

MAX_BYTES = 192 * 1024
PIECE_BYTES = 480  # divisible by three: padding appears only on the final line


def split_photo(jpeg):
    if len(jpeg) > MAX_BYTES:
        raise BoardError("Photo exceeds 192 KiB; resize or recompress the JPEG.")
    if len(jpeg) < 4 or not jpeg.startswith(b"\xff\xd8") or not jpeg.endswith(b"\xff\xd9"):
        raise BoardError("Photo must be a complete JPEG file.")
    for offset in range(0, len(jpeg), PIECE_BYTES):
        end = min(offset + PIECE_BYTES, len(jpeg))
        prefix = b">photo=" if end == len(jpeg) else b">photo+="
        yield prefix + base64.b64encode(jpeg[offset:end]) + b"\n", end


def expect(board, wanted, seconds):
    deadline = time.monotonic() + seconds
    while True:
        event = board.frame(deadline, "@photo")
        if event is None:
            raise BoardError("No photo acknowledgment from the board. Delivery is unconfirmed; no automatic retry was made.")
        if event.get("type") == "error":
            raise BoardError("Photo: " + event.get("code", "SEND_FAILED"))
        if event.get("type") == wanted:
            return event


def send_photo(board, jpeg):
    # Validate before making any device change.
    lines = list(split_photo(jpeg))
    board.drain(0.2)
    board.wake()
    board.write_line("photo.cancel")
    expect(board, "cancelled", 3)
    submitted = False
    try:
        for line, total in lines:
            board.write(line)
            submitted = line.startswith(b">photo=")
            ack = expect(board, "ack", 3)
            if ack.get("bytes") != total:
                raise BoardError("Photo bytes were lost on USB; delivery is unconfirmed.")
        expect(board, "accepted", 5)
        expect(board, "sent", 90)
    except BaseException:
        if not submitted:
            board.write_line("photo.cancel")
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("file", type=Path)
    parser.add_argument("--port")
    args = parser.parse_args()
    try:
        with args.file.open("rb") as image:
            jpeg = image.read(MAX_BYTES + 1)
        with Board(args.port or pick_port()) as board:
            send_photo(board, jpeg)
        print("Photo sent to Muse. Its reply appears in the Muse app.")
    except (BoardError, OSError) as error:
        sys.exit(str(error))


if __name__ == "__main__":
    main()
