#!/usr/bin/env python3
"""frame2png: sim display dumps (PBM/PGM) to PNG stills. Stdlib only.

Reads what simantic display models dump per refresh:
  - PBM (P4): 1-bit BW refreshes (1 = black, PBM convention)
  - PGM (P5): 8-bit grayscale refreshes (composed multi-plane frames)

and writes an 8-bit grayscale PNG. `--transpose` maps panel-memory
orientation to device orientation for panels wired with the gate/source
axes swapped relative to how the device is held — the Xteink X4's SSD1677
is one (device row = panel column): use it there, skip it for panels
scanned the way the device is held.

Examples:
  tools/frame2png.py run/xteink-epd-007.pgm --transpose        # -> ...007.png
  tools/frame2png.py run/*.pbm --transpose -d shots/
"""
from __future__ import annotations

import argparse
import struct
import sys
import zlib
from pathlib import Path


def read_pbm(data: bytes) -> tuple[int, int, bytes]:
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
    parts = data.split(maxsplit=4)
    assert parts[0] == b"P5", "not a binary PGM"
    w, h, maxval = int(parts[1]), int(parts[2]), int(parts[3])
    assert maxval == 255, "only 8-bit PGM supported"
    return w, h, parts[4][: w * h]


def transpose(w: int, h: int, pix: bytes) -> tuple[int, int, bytes]:
    out = bytearray(w * h)
    for y in range(w):        # output row = input column
        base = y * h
        for x in range(h):    # output column = input row
            out[base + x] = pix[x * w + y]
    return h, w, bytes(out)


def write_png(path: Path, w: int, h: int, pix: bytes) -> None:
    raw = b"".join(b"\x00" + pix[y * w:(y + 1) * w] for y in range(h))

    def chunk(typ: bytes, data: bytes) -> bytes:
        c = typ + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c))

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 6))
        + chunk(b"IEND", b""))


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("frames", nargs="+", type=Path, help="PBM/PGM dump files")
    ap.add_argument("--transpose", action="store_true",
                    help="panel-memory to device orientation (row <-> column)")
    ap.add_argument("-d", "--out-dir", type=Path, default=None,
                    help="output directory (default: beside each input)")
    args = ap.parse_args()

    for src in args.frames:
        data = src.read_bytes()
        if data[:2] == b"P4":
            w, h, pix = read_pbm(data)
        elif data[:2] == b"P5":
            w, h, pix = read_pgm(data)
        else:
            sys.exit(f"{src}: not a binary PBM/PGM")
        if args.transpose:
            w, h, pix = transpose(w, h, pix)
        dst = (args.out_dir or src.parent) / (src.stem + ".png")
        if args.out_dir:
            args.out_dir.mkdir(parents=True, exist_ok=True)
        write_png(dst, w, h, pix)
        print(f"{dst}: {w}x{h}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
