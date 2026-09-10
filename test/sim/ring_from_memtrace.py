"""Rebuild CrossPoint's RTC log ring from `sim --trace-memory` lines.

Run the CLI with `--trace-memory logHead --trace-memory logMessages:4096`
(symbols from `symbolsElfPath`, or `0x50000018:4` / `0x5000001c:4096`).
Every byte the firmware writes into the ring lands in output.txt as
`[t s] (MEM) logMessages write addr=0x5000xxxx value=0xNN pc=...`; a write to
logHead marks a completed message. This prints one line per message, stamped
with the virtual time the head advanced, so it follows a live run:

    python3 ring_from_memtrace.py output.txt            # once
    tail -f output.txt | python3 ring_from_memtrace.py - # live
"""
import re, sys
RING = 0x5000001C; SLOTS = 16; WIDTH = 256
MEM = re.compile(r"^\[(\d+\.\d+)s\] \(MEM\) (\S+) write addr=0x([0-9A-Fa-f]+) value=0x([0-9A-Fa-f]+)")
slots = [bytearray(WIDTH) for _ in range(SLOTS)]
cur = None            # slot the firmware is filling: logHead is bumped BEFORE the text is written
printed = set()
f = sys.stdin if sys.argv[1] == "-" else open(sys.argv[1], errors="replace")
for line in f:
    m = MEM.match(line)
    if not m: continue
    t, sym, addr, val = float(m.group(1)), m.group(2), int(m.group(3), 16), int(m.group(4), 16)
    if RING <= addr < RING + SLOTS * WIDTH:
        off = addr - RING; slot, i = off // WIDTH, off % WIDTH
        slots[slot][i] = val & 0xFF
        # the terminating NUL of a non-empty string completes the message
        if slot == cur and val & 0xFF == 0 and i > 0 and slot not in printed and slots[slot][0]:
            printed.add(slot)
            print(f"[{t:10.6f}s] {slots[slot][:i].decode('utf-8', 'replace')}", flush=True)
    elif sym == "logHead" or addr == 0x50000018:
        cur = val % SLOTS
        slots[cur] = bytearray(WIDTH); printed.discard(cur)
