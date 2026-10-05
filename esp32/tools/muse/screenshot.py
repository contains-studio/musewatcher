#!/usr/bin/env python3
"""Capture the current Watcher screen without resetting or changing its UI mode.

Usage: python screenshot.py PORT OUT.png
       python screenshot.py --self-test

Writes OUT.png (412x412) and OUT-3x.png (1236x1236). Requires pyserial for
hardware capture; PNG writing and offline tests use only the standard library.
The snapshot firmware has no checksum or row identifiers: valid-base64 pixel
substitutions cannot be detected. Missing/malformed chunks are never padded.
"""
from __future__ import annotations

import argparse
import base64
import binascii
from pathlib import Path
import re
import struct
import sys
import tempfile
import time
import zlib


class CaptureError(Exception):
    """Safe errors contain metadata only, never serial console content."""


ANSI = re.compile(rb"\x1b\[[0-?]*[ -/]*[@-~]")
HEADER = re.compile(rb"SNAP BEGIN (\d+) (\d+)(?: (\d+))?")
BASE64 = re.compile(rb"[A-Za-z0-9+/]+={0,2}")
BASE64_TAIL = re.compile(rb"[A-Za-z0-9+/]*={0,2}$")
LOG = re.compile(rb"(?:[EWIDV] \(\d+\) |@[A-Za-z][A-Za-z0-9_.-]* \{|--- )")


class Snapshot:
    def __init__(self):
        self.width = self.height = self.per_line = 0
        self.raw = bytearray()
        self.row = self.row_bytes = self.chunks = 0
        self.started = self.done = False

    def feed(self, line: bytes):
        # Do not include a rejected line in errors: this serial console can also
        # carry credentials or unrelated user content.
        line = line.rstrip(b"\r\n")
        clean = ANSI.sub(b"", line)  # Classification only: never edit pixel data.
        if self.done:
            raise CaptureError("Unexpected input after snapshot completion.")
        if clean == b"SNAP OFF":
            raise CaptureError("Screenshots are disabled in the running firmware.")
        if clean == b"SNAP ERROR":
            raise CaptureError("The Watcher could not start a screenshot. Retry when idle.")
        header = HEADER.fullmatch(clean)
        if header:
            if self.started:
                raise CaptureError("A second snapshot started before the first ended.")
            self.width, self.height = int(header[1]), int(header[2])
            self.per_line = int(header[3]) if header[3] else 144
            if (self.width, self.height) != (412, 412):
                raise CaptureError("Snapshot dimensions are not Watcher 412x412.")
            if not 1 <= self.per_line <= self.width * 2:
                raise CaptureError("Invalid snapshot chunk size.")
            self.started = True
            return
        if not self.started:
            return  # Drain old console output without retaining or printing it.
        if clean == b"SNAP END":
            if self.row != self.height or self.row_bytes or len(self.raw) != self.width * self.height * 2:
                raise CaptureError(f"Incomplete snapshot: {self.row}/{self.height} complete rows; no image saved.")
            self.done = True
            return
        if not line:
            return
        if self.row >= self.height:
            last_chunk = (self.width * 2) % self.per_line or self.per_line
            smallest = 4 * ((min(last_chunk, self.per_line) + 2) // 3)
            if len(BASE64_TAIL.search(line).group()) < smallest:
                return  # A log can finish after the last pixel write, too.
            raise CaptureError("Extra snapshot data before the end marker.")
        expected = min(self.per_line, self.width * 2 - self.row_bytes)
        encoded = 4 * ((expected + 2) // 3)
        # Snapshot writes are atomic and end with a newline; console logs may
        # prefix them. Extract the complete rightmost chunk before classifying
        # log lines. Do not strip ANSI first: a split ESC[ prefix could otherwise
        # swallow the first pixel character as its apparent CSI terminator.
        tail = BASE64_TAIL.search(line).group()
        if len(tail) < encoded:
            # A log may resume after a snapshot newline without its original
            # severity prefix. Seek the next complete atomic pixel write;
            # missing pixel writes still fail the final row/byte count.
            return
        payload = tail[-encoded:]
        try:
            data = base64.b64decode(payload, validate=True)
        except (ValueError, binascii.Error):
            raise CaptureError(f"Invalid base64 in row {self.row + 1}; no image saved.") from None
        if len(data) != expected or base64.b64encode(data) != payload:
            raise CaptureError(f"Invalid byte count or padding in row {self.row + 1}; no image saved.")
        self.raw.extend(data)
        self.chunks += 1
        self.row_bytes += len(data)
        if self.row_bytes == self.width * 2:
            self.row += 1
            self.row_bytes = 0

    def finish(self):
        if not self.done:
            if not self.started:
                raise CaptureError("Timed out before the Watcher started a screenshot.")
            raise CaptureError(f"Timed out with {self.row}/{self.height} complete rows and no end marker; no image saved.")
        return bytes(self.raw)


def png_bytes(raw: bytes, width: int, height: int, scale: int = 1) -> bytes:
    if len(raw) != width * height * 2 or scale < 1:
        raise CaptureError("Wrong pixel buffer size; no image saved.")
    image = bytearray()
    for y in range(height):
        row = bytearray(b"\x00")  # PNG filter: none.
        for x in range(width):
            v = struct.unpack_from("<H", raw, (y * width + x) * 2)[0]
            r, g, b = v >> 11, (v >> 5) & 63, v & 31
            rgb = bytes(((r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)))
            row.extend(rgb * scale)
        image.extend(row * scale)

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width * scale, height * scale, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(image)) + chunk(b"IEND", b""))


def save_png(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".watcher-snap-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def capture(port: str, timeout: float) -> Snapshot:
    from chat import Board, BoardError

    snapshot = Snapshot()
    try:
        with Board(port) as board:  # Clears HUPCL to prevent a reset on close.
            board.wake()           # Wake the UART; its first characters may be lost.
            board.write(b"w")      # Wake the screen, preserving the current view.
            before = time.monotonic() + 0.4
            while board.read_line(before) is not None:
                pass               # Discard preexisting output, never print it.
            board.write(b"p")      # The only command after wake: snapshot current UI.
            deadline = time.monotonic() + timeout
            while not snapshot.done:
                # Board reads max(1, in_waiting) with a 50ms timeout, rather than
                # waiting for a 64KiB read to fill while UART buffers overflow.
                line = board.read_line(deadline)
                if line is None:
                    break
                snapshot.feed(line.encode("utf-8"))
    except BoardError:
        raise CaptureError("Could not open or communicate with the Watcher serial port.") from None
    except OSError:
        raise CaptureError("Serial I/O failed; no image saved.") from None
    snapshot.finish()
    return snapshot


def self_test():
    import unittest

    def lines(raw):
        yield b"SNAP BEGIN 412 412 144"
        for y in range(412):
            row = raw[y * 824:(y + 1) * 824]
            for at in range(0, 824, 144):
                yield base64.b64encode(row[at:at + 144])
        yield b"SNAP END"

    class Tests(unittest.TestCase):
        raw = bytes(range(256)) * (412 * 412 * 2 // 256) + bytes(range((412 * 412 * 2) % 256))

        def test_device_capture_failure_is_immediate(self):
            for marker in (b"SNAP ERROR", b"SNAP OFF"):
                with self.assertRaises(CaptureError):
                    Snapshot().feed(marker)

        def test_complete_frame_with_private_interleaved_logs(self):
            snap = Snapshot()
            for line in lines(self.raw):
                snap.feed(b"\x1b[0;32mI (42) example: synthetic unrelated console text\x1b[0m")
                snap.feed(b" trailing fragment: done\x1b[0m")
                snap.feed(line + b"\r\n")
            self.assertEqual(snap.finish(), self.raw)

        def test_atomic_chunks_after_log_and_split_ansi_prefixes(self):
            snap = Snapshot()
            source = list(lines(self.raw))
            snap.feed(source[0])
            prefixes = (b"I (42) status: ", b"partiallogfragment", b" tail: ",
                        b"\x1b[0;32", b"\x1b[", b"\x1b[0;32mI (42) status: ")
            for i, chunk in enumerate(source[1:-1]):
                snap.feed(b"I (43) module: standalone synthetic log\r\n")
                snap.feed(prefixes[i % len(prefixes)] + chunk + b"\r\n")
            snap.feed(source[-1])
            self.assertEqual(snap.finish(), self.raw)

        def test_prefixed_missing_or_short_chunks_still_fail(self):
            original = list(lines(self.raw))
            for missing in (17, 96, 97, 102):
                with self.assertRaises(CaptureError):
                    snap = Snapshot()
                    snap.feed(original[0])
                    for i, chunk in enumerate(original[1:-1], 1):
                        if i != missing:
                            snap.feed(b"I (42) module: " + chunk)
                    snap.feed(original[-1])
                    snap.finish()
            snap = Snapshot()
            snap.feed(original[0])
            # Prefix is recognized as a log, but its incomplete payload must
            # never supply pixels; subsequent chunks must still expose the loss.
            snap.feed(b"I (42) module: " + original[1][8:])
            with self.assertRaises(CaptureError):
                for chunk in original[2:]:
                    snap.feed(chunk)
                snap.finish()

        def test_missing_full_chunk_short_row_and_duplicate_fail(self):
            original = list(lines(self.raw))
            for changed in (original[:2] + original[3:], original[:6] + original[7:],
                            original[:2] + original[1:]):
                with self.assertRaises(CaptureError):
                    snap = Snapshot()
                    for line in changed:
                        snap.feed(line)
                    snap.finish()

        def test_bad_base64_and_noncanonical_padding_fail(self):
            original = list(lines(self.raw))
            for at, bad in ((1, b"!" + original[1][1:]), (6, original[6][:-2] + b"B=")):
                changed = original.copy()
                changed[at] = bad
                with self.assertRaises(CaptureError):
                    snap = Snapshot()
                    for line in changed:
                        snap.feed(line)

        def test_missing_end_off_and_wrong_dimensions_fail(self):
            with self.assertRaises(CaptureError):
                Snapshot().feed(b"SNAP OFF")
            with self.assertRaises(CaptureError):
                Snapshot().feed(b"SNAP BEGIN 320 240 144")
            snap = Snapshot()
            for line in list(lines(self.raw))[:-1]:
                snap.feed(line)
            with self.assertRaises(CaptureError):
                snap.finish()

        def test_png_preserves_rgb565_colors_and_exact_scaling(self):
            pixels = struct.pack("<4H", 0xf800, 0x07e0, 0x001f, 0xffff)
            for scale in (1, 3):
                png = png_bytes(pixels, 2, 2, scale)
                self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
                at, compressed = 8, bytearray()
                while at < len(png):
                    n = struct.unpack_from(">I", png, at)[0]
                    tag, data = png[at + 4:at + 8], png[at + 8:at + 8 + n]
                    self.assertEqual(zlib.crc32(tag + data), struct.unpack_from(">I", png, at + 8 + n)[0])
                    if tag == b"IHDR":
                        self.assertEqual(struct.unpack_from(">II", data), (2 * scale, 2 * scale))
                    if tag == b"IDAT":
                        compressed.extend(data)
                    at += n + 12
                first = b"\x00" + b"\xff\x00\x00" * scale + b"\x00\xff\x00" * scale
                second = b"\x00" + b"\x00\x00\xff" * scale + b"\xff\xff\xff" * scale
                self.assertEqual(zlib.decompress(compressed), first * scale + second * scale)

    return unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Tests)).wasSuccessful()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("port", nargs="?", help="Watcher ESP32 console port (the CH342 port ending in 3)")
    parser.add_argument("output", nargs="?", type=Path, help="Original 412x412 PNG output path")
    parser.add_argument("--timeout", type=float, default=90, help="Capture deadline in seconds (default 90)")
    parser.add_argument("--self-test", action="store_true", help="Run offline synthetic parser/PNG tests; no serial access")
    args = parser.parse_args()
    if args.self_test:
        return 0 if self_test() else 1
    if not args.port or not args.output:
        parser.error("port and output are required for capture")
    if args.timeout <= 0:
        parser.error("timeout must be positive")
    try:
        snapshot = capture(args.port, args.timeout)
        raw = snapshot.finish()
        original = args.output.resolve()
        enlarged = original.with_name(original.stem + "-3x.png")
        if original == enlarged:
            raise CaptureError("Original and enlarged output paths must differ.")
        original_png = png_bytes(raw, snapshot.width, snapshot.height)
        enlarged_png = png_bytes(raw, snapshot.width, snapshot.height, 3)
        save_png(original, original_png)
        save_png(enlarged, enlarged_png)
    except (CaptureError, OSError) as error:
        print(f"Screenshot failed: {error}", file=sys.stderr)
        return 1
    print(f"Saved {original} (412x412; {len(raw)} verified RGB565 bytes).")
    print(f"Saved {enlarged} (1236x1236; exact 3x nearest-neighbor enlargement).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
