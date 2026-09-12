#!/usr/bin/env python3
"""Bench lint: a wall-clock sleep of a second or more is a wait, not a poll
cadence, and waits in this directory are in virtual seconds. Mark a legitimate
one (waiting for a host port to open) with ``# wall-ok``. Also every script
that starts the simulator must go through guard.observer_args()."""
import pathlib, re, sys
HERE = pathlib.Path(__file__).resolve().parent
bad = []
for f in sorted(HERE.glob("*.py")):
    if f.name in ("guard.py", "lint_bench.py"): continue
    src = f.read_text()
    for n, line in enumerate(src.splitlines(), 1):
        m = re.search(r"time\.sleep\(\s*([0-9.]+)\s*\)", line)
        if m and float(m.group(1)) >= 1.0 and "wall-ok" not in line:
            bad.append(f"{f.name}:{n}: wall-clock wait time.sleep({m.group(1)}); use virtual time or mark # wall-ok")
    if "--scenario" in src and "subprocess.Popen" in src and "observer_args()" not in src:
        bad.append(f"{f.name}: starts the simulator without guard.observer_args()")
print("\n".join(bad) if bad else "lint_bench: ok")
sys.exit(1 if bad else 0)
