#!/bin/sh
# Runs the committed X4 (ESP32-C3) CrossPoint image on the simulator, unpaced: boot -> Home -> FileBrowser ->
# demo.epub -> two page turns (scripted ADC buttons, sim/pages.yaml). Pass = those activities were entered,
# the reader saved page 2, and no fault lines. SD image is built from sim/mkepub.py with mtools.
cd "$(dirname "$0")/.." || exit 1
SIM=${SIM:-sim}
O=$(mktemp -d) || exit 1
trap 'rm -rf "$O"' EXIT
export EPD_DUMP="$O/epd" SD_IMAGE="$O/sd.img"
mkdir "$O/sdc"
python3 sim/mkepub.py "$O/sdc/demo.epub" || exit 1
printf 'Plain text file on the simulated card.\n' > "$O/sdc/notes.txt"
mformat -C -i "$SD_IMAGE" -T 131072 -F -c 1 :: && mcopy -i "$SD_IMAGE" "$O/sdc/demo.epub" "$O/sdc/notes.txt" :: || exit 1
"$SIM" run --scenario sim/pages.yaml --ascii > "$O/uart.log" 2> "$O/sim.log"
rc=0
for a in "SD card detected" "Entering activity: Home" "Entering activity: FileBrowser" "Entering activity: EpubReader"; do
  if grep -qF "$a" "$O/uart.log"; then echo "ok   $a"; else echo "FAIL $a"; rc=1; fi
done
if grep -qE 'Progress saved: spine=[0-9]+ offset=[0-9]+ page=2' "$O/uart.log"; then echo "ok   page 2 saved"; else echo "FAIL page 2 saved"; rc=1; fi
if grep -qE 'Guru Meditation|panic|abort\(\)|assert failed|Backtrace|HardFault' "$O/uart.log"; then echo "FAIL fault lines"; rc=1; else echo "ok   no faults"; fi
exit $rc
