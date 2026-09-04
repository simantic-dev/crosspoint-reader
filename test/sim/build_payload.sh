#!/usr/bin/env bash
# Build the payload the simulator loads, from a PlatformIO `-e sim` build.
#
# The replx boots the real ESP32-C3 mask ROM, which reads the app out of SPI
# flash exactly as silicon does, so the simulator's --elf is not firmware.elf.
# It is a container carrying three flat regions as ELF segments:
#
#     0x40000000  mask ROM code (384 KB)
#     0x3ff00000  mask ROM data window (128 KB)
#     0x30000000  flash image: bootloader + partition table + app
#
# Handing it firmware.elf instead yields a run that is completely SILENT --
# no banner, no panic -- because the machine starts with no ROM to boot
# through. Verify with:
#
#     riscv32-esp-elf-readelf -lW <payload> | grep LOAD
#
# A correct payload prints the ROM's own banner as its first line:
#
#     ESP-ROM:esp32c3-api1-20210111-dirty
#
# The mask ROM is Espressif's proprietary ROM and is NOT in this repo. Supply a
# raw dump; qemu-espressif ships one as pc-bios/esp32c3-rom.bin. The *debug* ELF
# bundled with ESP-IDF is not a substitute -- its .data initializers sit
# relocated, so a naive flatten reads zero where the ROM's _init copies from.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
BUILD="$REPO/.pio/build/sim"
OUT="${2:-$REPO/test/payloads/crosspoint-featbt-latest-sim.elf}"
ROM="${1:-${ESP32C3_ROM:-}}"

if [[ -z "$ROM" || ! -f "$ROM" ]]; then
  echo "usage: $0 <esp32c3-rom.bin> [out.elf]   (or set ESP32C3_ROM)" >&2
  exit 2
fi
if [[ ! -f "$BUILD/firmware.bin" ]]; then
  echo "no $BUILD/firmware.bin -- run: pio run -e sim" >&2
  echo "(-e sim, not -e default: the default env puts the console on USB-CDC," >&2
  echo " which the simulator's UART capture cannot see)" >&2
  exit 2
fi

PIO_PY="${PIO_PY:-$HOME/.platformio/penv/bin/python}"
ESPTOOL="${ESPTOOL:-$HOME/.platformio/packages/tool-esptoolpy/esptool.py}"
# Offsets are the ESP32 convention and match partitions.csv: app0 starts at
# 0x10000, the partition table at 0x8000.
"$PIO_PY" "$ESPTOOL" --chip esp32c3 merge-bin \
  --output "$OUT.flash" --flash-mode dio --flash-size 16MB \
  0x0 "$BUILD/bootloader.bin" \
  0x8000 "$BUILD/partitions.bin" \
  0x10000 "$BUILD/firmware.bin"

# merge-bin stops at the last byte written; the Xteink X4 carries a 16 MB
# W25Q128-class part and the replx maps the whole region, so pad to size.
"$PIO_PY" - "$OUT.flash" <<'PY'
import pathlib, sys
p = pathlib.Path(sys.argv[1]); d = bytearray(p.read_bytes())
d.extend(b"\xff" * (0x1000000 - len(d)))
p.write_bytes(bytes(d))
PY

BUILDER="${SIMANTIC_C3_IMAGE_BUILDER:-}"
if [[ -z "$BUILDER" ]]; then
  echo "set SIMANTIC_C3_IMAGE_BUILDER to esp32c3_build_image.py (simantic mcu-lib)" >&2
  exit 2
fi
"$PIO_PY" "$BUILDER" --rom "$ROM" --flash "$OUT.flash" -o "$OUT"
rm -f "$OUT.flash"
echo "payload: $OUT"
