#!/bin/sh
# Sim addition. Boots the built image on the simulated ESP32-C3 with the SSD1677 panel peer wired (sim/epd.board)
# for N virtual seconds (default 8), checks the firmware console, then checks that at least one frame was dumped
# and is not blank. Frames and PNGs land in sim/out/. Needs `sim` on PATH (or SIM=...), python3 and sim/build.sh output.
# SD=1 uses sim/scenario-sd.yaml (panel + SD card behind an SPI multiplexer): that configuration does not work
# yet, see sim/README.md.
set -e
cd "$(dirname "$0")/.."
SIM=${SIM:-sim}
mkdir -p sim/out
rm -f sim/out/epd-*
SCEN=sim/scenario.yaml
if [ -n "$SD" ]; then
  SCEN=sim/scenario-sd.yaml
  SD_IMAGE=${SD_IMAGE:-sim/out/sd.img}
  export SD_IMAGE
  mkdir -p sim/out/sdc
  python3 sim/mkepub.py sim/out/sdc/demo.epub
  rm -f "$SD_IMAGE"
  mformat -C -i "$SD_IMAGE" -T 131072 -F -c 1 ::
  mcopy -i "$SD_IMAGE" sim/out/sdc/demo.epub ::
fi
export EPD_DUMP=sim/out/epd
"$SIM" run --scenario "$SCEN" --timeout "${1:-8}" --ascii \
  --expect "Xteink probe scores" --expect "Entering activity: FullScreenMessage" \
  --expect-absent "Guru Meditation" > sim/out/uart.log 2> sim/out/sim.log
set -- sim/out/epd-*.pbm
[ -f "$1" ] || { echo "FAIL   no frame captured"; exit 1; }
for f in "$@"; do
  python3 sim/frame2png.py "$f" --transpose -d sim/out
  # PBM payload after the 2-line header: all-zero (all white) or all-0xFF (all black) means blank
  tail -c +12 "$f" | python3 -c 'import sys;d=sys.stdin.buffer.read();sys.exit(0 if len(set(d))>1 else 1)' \
    || { echo "FAIL   $f is blank"; exit 1; }
done
echo "PASS   $# frame(s) in sim/out/, log in sim/out/uart.log"
