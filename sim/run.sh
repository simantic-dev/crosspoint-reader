#!/bin/sh
# Sim addition. Full showcase: boots the built image on the simulated ESP32-C3 with the SD card and SSD1677 panel
# (sim/x4.board), presses the front buttons through `sim --control-port` (sim/drive.py), converts every panel
# refresh to PNG in sim/out/, and checks the firmware reached the file browser and the reader.
# Needs `sim` on PATH (or SIM=...), python3, mtools and sim/build.sh output. Runs at wall-clock speed (--real-time).
#   MUX=1 sim/run.sh   the older SPIMultiplexer configuration (sim/scenario-sd.yaml); known not to reach the card.
set -e
cd "$(dirname "$0")/.."
SIM=${SIM:-sim}
IMG=.pio/build/default/firmware.factory.bin
mkdir -p sim/out/sdc
rm -f sim/out/epd-* sim/out/sd.img
export EPD_DUMP=sim/out/epd SD_IMAGE=sim/out/sd.img
python3 sim/mkepub.py sim/out/sdc/demo.epub
printf 'Plain text file on the simulated card.\n' > sim/out/sdc/notes.txt
mformat -C -i "$SD_IMAGE" -T 131072 -F -c 1 ::
mcopy -i "$SD_IMAGE" sim/out/sdc/demo.epub sim/out/sdc/notes.txt ::
if [ -n "$MUX" ]; then
  "$SIM" run --scenario sim/scenario-sd.yaml --timeout "${1:-12}" --ascii > sim/out/uart.log 2> sim/out/sim.log || true
  grep -E 'SD card' sim/out/uart.log | head -3; exit 0
fi
"$SIM" ESP32-C3 run --elf "$IMG" --board sim/x4.board --timeout "${1:-50}" --control-port 21777 --real-time --ascii \
  > sim/out/uart.log 2> sim/out/sim.log &
PID=$!
python3 sim/drive.py 21777 wait:4,confirm:6,confirm:12,right:6,right:6,wait:2 > sim/out/drive.log
wait $PID || true
set -- sim/out/epd-*.pbm
[ -f "$1" ] || { echo "FAIL   no frame captured"; exit 1; }
for f in "$@"; do
  python3 sim/frame2png.py "$f" --transpose -d sim/out
  tail -c +12 "$f" | python3 -c 'import sys;d=sys.stdin.buffer.read();sys.exit(0 if len(set(d))>1 else 1)' \
    || { echo "FAIL   $f is blank"; exit 1; }
done
for a in "SD card detected" "Entering activity: FileBrowser" "Entering activity: EpubReader"; do
  grep -q "$a" sim/out/uart.log || { echo "FAIL   missing: $a"; exit 1; }
done
echo "PASS   $# frame(s) in sim/out/, log in sim/out/uart.log"
