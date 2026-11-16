#!/bin/sh
# Sim addition. Builds the unmodified `default` env (ESP32-C3, Xteink X4/X3) and prints the merged flash image path.
# Needs PlatformIO Core 6.1.19 (the pioarduino platform refuses 6.1.18; 6.2.0 failed here with
# "No module named 'SCons.Tool.FortranCommon'"). Set PIO to that binary; PLATFORMIO_CORE_DIR may point at a scratch dir.
set -e
cd "$(dirname "$0")/.."
git submodule update --init --depth 1
${PIO:-pio} run -e default
echo ".pio/build/default/firmware.factory.bin"
