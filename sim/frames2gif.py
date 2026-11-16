#!/usr/bin/env python3
"""frames2gif: animated GIF from sim display frames. Stdlib only.

Turns a sequence of display-frame files into a looping GIF89a — built for
e-ink/LCD frames coming out of simantic display models, so it reads exactly
what the pipeline produces:

  - PBM (P4)  : 1-bit dumps (SSD1677 BW refreshes; 1 = black, PBM convention)
  - PGM (P5)  : 8-bit grayscale dumps (grayscale refreshes)
  - PNG       : grayscale, color type 0, bit depth 1 or 8, as written by the
                repo's stdlib PNG writers (pbm2png.py and friends)

All frames must share one geometry. Pixels are quantized to a 4-level gray
palette (an e-ink panel's worth); ordering is 0=black .. 3=white.

`--flash` inserts a brief inverted frame before each screen — the full-refresh
inversion flash an e-ink panel really shows between screens. It is a
stylization here (the real inversion pass has its own content), so turn it
off with `--flash 0` when frame-accuracy matters more than looking right.

Examples:
  tools/frames2gif.py shots/01.png shots/02.png -o journey.gif
  tools/frames2gif.py run/xteink-epd-*.pbm -o boot.gif --hold 0.6 --flash 0
"""
from __future__ import annotations

import argparse
import struct
import sys
import zlib
from pathlib import Path

PALETTE = (0x11, 0x60, 0xAB, 0xF4)  # 4 grays, slightly warm-white/soft-black


def read_pbm(data: bytes) -> tuple[int, int, bytes]:
    # P4\n<w> <h>\n<packed bits>; 1 = black.
    parts = data.split(maxsplit=3)
    assert parts[0] == b"P4", "not a binary PBM"
    w, h = int(parts[1]), int(parts[2])
    bits = parts[3]
    stride = (w + 7) // 8
    out = bytearray(w * h)
    for y in range(h):
        row = bits[y * stride:(y + 1) * stride]
        base = y * w
        for x in range(w):
            black = (row[x >> 3] >> (7 - (x & 7))) & 1
            out[base + x] = 0x00 if black else 0xFF
    return w, h, bytes(out)


def read_pgm(data: bytes) -> tuple[int, int, bytes]:
    # P5\n<w> <h>\n<maxval>\n<bytes>
    parts = data.split(maxsplit=4)
    assert parts[0] == b"P5", "not a binary PGM"
    w, h, maxval = int(parts[1]), int(parts[2]), int(parts[3])
    assert maxval == 255, "only 8-bit PGM supported"
    return w, h, parts[4][: w * h]


def read_png_gray(data: bytes) -> tuple[int, int, bytes]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    pos, w, h, idat, bitdepth, ctype = 8, 0, 0, b"", 0, 0
    while pos < len(data):
        ln, typ = struct.unpack(">I4s", data[pos:pos + 8])
        chunk = data[pos + 8:pos + 8 + ln]
        if typ == b"IHDR":
            w, h, bitdepth, ctype = struct.unpack(">IIBB", chunk[:10])
        elif typ == b"IDAT":
            idat += chunk
        pos += 12 + ln
    assert ctype == 0 and bitdepth in (1, 8), (
        f"only grayscale PNG (type 0, depth 1/8) supported, got type={ctype} depth={bitdepth}")
    raw = zlib.decompress(idat)
    stride = w if bitdepth == 8 else (w + 7) // 8
    out = bytearray(w * h)
    prev = bytearray(stride)
    for y in range(h):
        f = raw[y * (stride + 1)]
        row = bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        if f == 1:  # Sub
            for x in range(1, stride):
                row[x] = (row[x] + row[x - 1]) & 0xFF
        elif f == 2:  # Up
            for x in range(stride):
                row[x] = (row[x] + prev[x]) & 0xFF
        elif f != 0:
            raise ValueError(f"PNG filter {f} unsupported (writers here use 0)")
        if bitdepth == 8:
            out[y * w:(y + 1) * w] = row
        else:
            base = y * w
            for x in range(w):
                bit = (row[x >> 3] >> (7 - (x & 7))) & 1
                out[base + x] = 0xFF if bit else 0x00
        prev = row
    return w, h, bytes(out)


def read_frame(path: Path) -> tuple[int, int, bytes]:
    data = path.read_bytes()
    if data[:2] == b"P4":
        return read_pbm(data)
    if data[:2] == b"P5":
        return read_pgm(data)
    return read_png_gray(data)


def lzw_encode(indices: bytes, min_code_size: int) -> bytes:
    clear = 1 << min_code_size
    end = clear + 1
    table = {bytes([i]): i for i in range(clear)}
    dict_size = end + 1
    code_size = min_code_size + 1
    out = bytearray()
    acc = nbits = 0

    def emit(code: int, size: int) -> None:
        nonlocal acc, nbits
        acc |= code << nbits
        nbits += size
        while nbits >= 8:
            out.append(acc & 0xFF)
            acc >>= 8
            nbits -= 8

    emit(clear, code_size)
    buf = b""
    for b in indices:
        nxt = buf + bytes([b])
        if nxt in table:
            buf = nxt
            continue
        emit(table[buf], code_size)
        table[nxt] = dict_size
        dict_size += 1
        if dict_size == (1 << code_size) + 1:
            if code_size < 12:
                code_size += 1
            else:
                emit(clear, code_size)
                table = {bytes([i]): i for i in range(clear)}
                dict_size = end + 1
                code_size = min_code_size + 1
        buf = bytes([b])
    if buf:
        emit(table[buf], code_size)
    emit(end, code_size)
    if nbits:
        out.append(acc & 0xFF)
    return bytes(out)


def frame_block(indices: bytes, w: int, h: int, delay_cs: int) -> bytes:
    # Graphic control (disposal=leave, no transparency) + image descriptor.
    gce = b"\x21\xf9\x04\x04" + struct.pack("<H", delay_cs) + b"\x00\x00"
    desc = b"\x2c" + struct.pack("<HHHH", 0, 0, w, h) + b"\x00"
    min_code_size = 2
    lzw = lzw_encode(indices, min_code_size)
    blocks = bytearray([min_code_size])
    for i in range(0, len(lzw), 255):
        chunk = lzw[i:i + 255]
        blocks.append(len(chunk))
        blocks += chunk
    blocks.append(0)
    return gce + desc + bytes(blocks)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("frames", nargs="+", type=Path,
                    help="frame files in display order (PBM/PGM/grayscale PNG)")
    ap.add_argument("-o", "--out", type=Path, required=True, help="output .gif")
    ap.add_argument("--hold", type=float, default=1.3,
                    help="seconds each frame stays on screen (default 1.3)")
    ap.add_argument("--flash", type=float, default=0.09,
                    help="seconds of inverted flash before each new frame; "
                         "0 disables (default 0.09)")
    args = ap.parse_args()

    frames = [read_frame(p) for p in args.frames]
    w, h = frames[0][0], frames[0][1]
    for path, (fw, fh, _) in zip(args.frames, frames):
        if (fw, fh) != (w, h):
            sys.exit(f"{path}: {fw}x{fh} does not match first frame {w}x{h}")

    gif = bytearray()
    gif += b"GIF89a" + struct.pack("<HHBBB", w, h, 0x91, 0, 0)
    for v in PALETTE:
        gif += bytes([v, v, v])
    gif += b"\x21\xff\x0bNETSCAPE2.0\x03\x01\x00\x00\x00"  # loop forever

    hold_cs = max(2, round(args.hold * 100))
    flash_cs = max(2, round(args.flash * 100))
    for i, (_, _, gray) in enumerate(frames):
        q = bytes(p >> 6 for p in gray)
        if i > 0 and args.flash > 0:
            gif += frame_block(bytes(3 - p for p in q), w, h, flash_cs)
        gif += frame_block(q, w, h, hold_cs)
    gif += b"\x3b"

    args.out.write_bytes(gif)
    print(f"{args.out} ({len(gif) / 1024:.0f} KiB, {len(frames)} frames, "
          f"{w}x{h})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
