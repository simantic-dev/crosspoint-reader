"""Guardrails every bench script in this directory goes through.

Each one exists because a run without it produced a wrong answer once:

- pristine_image(): the engine writes the SD image back, so a run left behind
  ``bluetoothEnabled=true`` and a warm section cache for the next one.
- observer_args(): the RTC-ring watchpoints and ``--memory-stats`` sampler
  perturb FreeRTOS wake-ups (simantic-core#330); a 30 s "panel stall" was the
  observers. Any timing claim needs a run with ``NO_OBSERVERS=1`` beside it.
- RatioGate: a 62-minute run was accepted as normal. Wall/virtual above the
  gate is a model gap or a bad harness, never a fact of life.
- require(): a wrong menu row turned 80 pages with BLE never requested and
  read as a null result. Navigation that cannot prove where it landed aborts.
"""
import os, shutil, stat, sys, time

OBSERVERS = ["--memory-stats", "100ms", "--trace-memory", "logHead", "--trace-memory", "logMessages:4096"]

def pristine_image(base, run):
    """Copy the canonical SD image to the run image and make the canonical one
    read-only, so no harness can write into it by accident."""
    if not base.exists():
        sys.exit(f"no pristine SD image at {base}; build it with make_sdcard.sh")
    base.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    run.unlink(missing_ok=True)
    shutil.copyfile(base, run)
    return run

def observer_args():
    """The observer set for this run. ``NO_OBSERVERS=1`` drops all of them,
    ``EXTRA_ARGS`` replaces them verbatim. The choice is printed so a result
    file always says which run it was."""
    if os.environ.get("NO_OBSERVERS"):
        args = []
    else:
        args = [a for a in os.environ.get("EXTRA_ARGS", " ".join(OBSERVERS)).split() if a]
    print("[guard] observers:", " ".join(args) if args else "NONE (control run)", flush=True)
    return args

def observer_note():
    on = not os.environ.get("NO_OBSERVERS")
    return ("observers ON: any timing in this report needs a NO_OBSERVERS=1 control run beside it"
            if on else "observers OFF: control run, no ring text available")

class RatioGate:
    """Wall/virtual ratio at exit. Above ``RATIO_MAX`` (default 3) the run
    fails unless ``ALLOW_SLOW=<reason>`` is set; the reason is printed."""
    def __init__(self, vnow):
        self.t0 = time.time(); self.vnow = vnow
        self.max = float(os.environ.get("RATIO_MAX", "3"))
    def check(self):
        v = self.vnow(); w = time.time() - self.t0
        ratio = w / v if v > 0 else float("inf")
        line = f"[guard] wall {w:.0f}s / virtual {v:.1f}s = {ratio:.2f}x (gate {self.max}x)"
        reason = os.environ.get("ALLOW_SLOW")
        if ratio > self.max and not reason:
            print(line, "FAIL: over the gate; set ALLOW_SLOW=<reason> only with a reason worth writing down", flush=True)
            return 2
        print(line, f"(ALLOW_SLOW: {reason})" if reason else "", flush=True)
        return 0

def require(ok, what):
    """Abort loudly. A navigation step that cannot prove where it landed must
    not be followed by a measurement."""
    if not ok:
        sys.exit(f"[guard] ABORT: {what}")

def pristine_platform(replx, base, run):
    """Point a copy of ``replx`` at a fresh copy of ``base`` and return the
    copy's file name. The canonical image is never the one the engine opens."""
    import pathlib, re
    replx = pathlib.Path(replx)
    pristine_image(base, run)
    text = replx.read_text()
    new, n = re.subn(r'imageFile:\s*"[^"]+"', f'imageFile: "{run.name}"', text)
    if n != 1:
        sys.exit(f"[guard] {replx.name}: expected exactly one imageFile:, found {n}")
    out = replx.with_name(replx.stem + "-run.replx")
    out.write_text(new)
    return out.name
