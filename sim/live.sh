#!/bin/sh
# Sim addition. Interactive: boots the built image like sim/run.sh (same SD card and panel), with no scripted
# presses and no time limit, and opens sim/reader.html, which shows the panel and presses the buttons live over
# the control port (21777). Needs `sim` on PATH (or SIM=...), python3, mtools and sim/build.sh output. Ctrl-C stops.
set -e
cd "$(dirname "$0")/.."
SIM=${SIM:-sim}
IMG=.pio/build/default/firmware.factory.bin
[ -f "$IMG" ] || sh sim/build.sh > /dev/null
mkdir -p sim/out/sdc
rm -f sim/out/epd-* sim/out/sd.img
export EPD_DUMP=sim/out/epd SD_IMAGE=sim/out/sd.img
python3 sim/mkepub.py sim/out/sdc/demo.epub
printf 'Plain text file on the simulated card.\n' > sim/out/sdc/notes.txt
mformat -C -i "$SD_IMAGE" -T 131072 -F -c 1 ::
mcopy -i "$SD_IMAGE" sim/out/sdc/demo.epub sim/out/sdc/notes.txt ::
"$SIM" run --scenario sim/live.yaml --ascii > sim/out/uart.log 2> sim/out/sim.log &
PID=$!
trap 'kill $PID 2>/dev/null' EXIT INT TERM
URL="file://$PWD/sim/reader.html?port=21777"
(command -v open >/dev/null && open "$URL") || (command -v xdg-open >/dev/null && xdg-open "$URL") || echo "open $URL"
echo "CrossPoint running (sim pid $PID). Ctrl-C to stop."
wait $PID
