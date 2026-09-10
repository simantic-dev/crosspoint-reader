"""Turn '(TRACE) logPrintf ... a0=<level> a1=<tag> a2=<fmt>' lines from a CLI output.txt
into readable log lines by reading the C strings out of the ELF's .flash.rodata.
Usage: resolve_logs.py output.txt firmware.elf"""
import re, sys, subprocess, glob, os
out, elf = sys.argv[1], sys.argv[2]
od = glob.glob(os.path.expanduser("~/.platformio/packages/toolchain-riscv32-esp*/bin/riscv32-esp-elf-objdump"))[0]
secs = []
for l in subprocess.run([od, "-h", elf], capture_output=True, text=True).stdout.splitlines():
    f = l.split()
    if len(f) >= 6 and f[1].startswith(".flash.rodata") and not f[1].endswith("_dummy"):
        secs.append((int(f[3], 16), int(f[2], 16), int(f[5], 16)))   # vma, size, file offset
data = open(elf, "rb").read()
def cstr(addr):
    for vma, size, off in secs:
        if vma <= addr < vma + size:
            i = off + (addr - vma); j = data.index(b"\0", i)
            return data[i:j].decode("utf-8", "replace")
    return f"<0x{addr:x}>"
TR = re.compile(r"^\[(\d+\.\d+)s\] \(TRACE\) logPrintf .* a0=0x([0-9A-F]+) a1=0x([0-9A-F]+) a2=0x([0-9A-F]+)")
for l in open(out, errors="replace"):
    m = TR.match(l)
    if m:
        print(f"[{float(m.group(1)):9.3f}s] [{cstr(int(m.group(2),16))}] [{cstr(int(m.group(3),16))}] {cstr(int(m.group(4),16))}")
