#!/bin/sh
# Sim addition. Boots the built image on the simulated ESP32-C3 with no board file (nothing wired to the pins)
# for N virtual seconds (default 4) and checks the firmware's own console. Needs `sim` on PATH (or SIM=...)
# and sim/build.sh output. Log: sim/out/uart.log
set -e
cd "$(dirname "$0")/.."
SIM=${SIM:-sim}
mkdir -p sim/out
"$SIM" ESP32-C3 run --elf .pio/build/default/firmware.factory.bin --timeout "${1:-4}" --ascii \
  --expect "Xteink probe scores" --expect "[XTDET] bus probe VER=FF FF FF FF FF FLG=FF -> default controller" \
  --expect-absent "Guru Meditation" > sim/out/uart.log 2> sim/out/sim.log
echo "PASS   log in sim/out/uart.log"
