"""Shared harness for the Xteink BLE benches.

Three things the bench needs that `Sim` cannot give us directly:

* **Symbols.** The payload the simulator boots is a flash-image container and is
  STRIPPED, so `sim.symbol()` resolves nothing. The matching symbol table lives
  in the PlatformIO build's `firmware.elf`; `symbols()` reads it with `nm` so
  tests can name functions instead of hard-coding addresses.

* **Firmware logs.** This build's serial console stops early, so log-based
  assertions silently read a dead channel. CrossPoint keeps its own RTT-style
  ring buffer in RTC memory (`logMessages`), written by `logPrintf` BEFORE it
  touches the console, so it survives both a dead console and a panic reboot.
  `ring()` reads it over the bus while the emulation is paused -- non-halting,
  no perturbation.

* **A payload gate.** A container packed with the wrong ESP32-C3 mask ROM has a
  zeroed PHY jump table, and every radio enable hangs. `assert_good_rom()`
  fails loudly at collection instead of leaving a mysterious timeout.
"""

from __future__ import annotations

import os
import re
import subprocess
from functools import lru_cache
from pathlib import Path

#: The ROM's PHY dispatch table. A payload built from the qemu-open-mac dump has
#: 228 zeroed bytes here, so `esp_bt_controller_enable` jumps to 0 and hangs.
PHY_TABLE_OFFSET = 0x94 + 0x1C34
PHY_TABLE_EXPECTED = bytes.fromhex("6fa0d33e")

#: `RTC_NOINIT_ATTR` ring buffer in lib/Logging/Logging.cpp. Addresses are stable
#: across builds (RTC fast memory, first three objects), but are verified against
#: the symbol table by `ring()` when an ELF is supplied.
RING_MAGIC_VALUE = 0xDEADBEEF
RING_LINES = 16
RING_WIDTH = 256

BUTTON = {"back": 0, "confirm": 1, "left": 2, "right": 3, "up": 4, "down": 5}
PRESS_SECONDS = 0.4


def _nm() -> str:
    for base in sorted(Path.home().glob(".espressif/tools/riscv32-esp-elf/*"), reverse=True):
        cand = base / "riscv32-esp-elf" / "bin" / "riscv32-esp-elf-nm"
        if cand.exists():
            return str(cand)
    raise RuntimeError("riscv32-esp-elf-nm not found under ~/.espressif")


@lru_cache(maxsize=4)
def symbols(elf: str) -> dict[str, int]:
    """name -> address, for every symbol in `elf` (the PlatformIO firmware.elf)."""
    out = subprocess.run([_nm(), "-C", elf], capture_output=True, text=True, check=True).stdout
    table: dict[str, int] = {}
    for line in out.splitlines():
        m = re.match(r"^([0-9a-fA-F]+)\s+\S\s+(.+)$", line)
        if m:
            table.setdefault(m.group(2).strip(), int(m.group(1), 16))
    return table


def addr(elf: str, name: str) -> int:
    """Address of `name`, or KeyError with near-misses to make typos obvious."""
    table = symbols(elf)
    if name in table:
        return table[name]
    near = [k for k in table if name in k][:8]
    raise KeyError(f"{name!r} not in {elf}" + (f"; did you mean {near}?" if near else ""))


def assert_good_rom(payload: Path) -> None:
    """Fail loudly if the payload carries the mask ROM with the zeroed PHY table."""
    with open(payload, "rb") as fh:
        fh.seek(PHY_TABLE_OFFSET)
        got = fh.read(4)
    if got != PHY_TABLE_EXPECTED:
        raise AssertionError(
            f"{payload} has a bad ESP32-C3 mask ROM: PHY jump table reads {got.hex()}, "
            f"want {PHY_TABLE_EXPECTED.hex()}. Repack with the espressif dump "
            f"(~/.sim_cache/roms/esp32c3-rom.bin) -- every radio enable hangs otherwise."
        )


class Ring:
    """Reader for CrossPoint's in-RAM log ring buffer."""

    def __init__(self, elf: str | None = None):
        if elf:
            table = symbols(elf)
            self.magic = table["rtcLogMagic"]
            self.head = table["logHead"]
            self.msgs = table["logMessages"]
        else:
            self.magic, self.head, self.msgs = 0x50000014, 0x50000018, 0x5000001C
        self._seen: list[str] = []

    def _u32(self, sim, address: int) -> int:
        return int.from_bytes(sim.read_memory(address, 4), "little")

    def lines(self, sim) -> list[str]:
        """The 16-line window, oldest first. Empty if the buffer is uninitialised."""
        if self._u32(sim, self.magic) != RING_MAGIC_VALUE:
            return []
        head = self._u32(sim, self.head)
        raw = bytes(sim.read_memory(self.msgs, RING_LINES * RING_WIDTH))
        out = []
        for i in range(RING_LINES):
            idx = (head + i) % RING_LINES
            text = raw[idx * RING_WIDTH:(idx + 1) * RING_WIDTH].split(b"\0")[0]
            text = text.decode("utf-8", "replace").strip()
            if text:
                out.append(text)
        return out

    def new(self, sim) -> list[str]:
        """Lines not returned by a previous `new()`.

        The window only holds 16 lines, so a burst between calls is lost -- this
        de-duplicates, it does not reconstruct a full transcript.

        Reboot-aware: boot is deterministic, so after a warm reset (e.g.
        CrossPoint's SilentRestart) the new boot lines are byte-identical to
        the first boot's and content-dedup would swallow them -- a run that
        rebooted would look like a run that went silent. `logHead` moving
        backwards, or the magic being re-initialised, marks a reboot; the seen
        set is cleared and a synthetic marker line is emitted so callers can
        count reboots without guessing from boot-time text.
        """
        head = self._u32(sim, self.head)
        last = getattr(self, "_last_head", None)
        rebooted = last is not None and head < last and (last - head) > 1
        self._last_head = head
        current = self.lines(sim)
        if rebooted:
            self._seen = []
            self.reboots = getattr(self, "reboots", 0) + 1
            fresh = ["<<< RING: logHead went %d -> %d: warm reboot #%d >>>" % (last, head, self.reboots)]
        else:
            fresh = []
        fresh += [l for l in current if l not in self._seen]
        self._seen = (self._seen + [l for l in fresh if not l.startswith("<<<")])[-256:]
        return fresh

    def head_index(self, sim) -> int:
        """`logHead`. A value that stops advancing is the wedge signal."""
        return self._u32(sim, self.head)


def press(sim, name: str, settle: float, peripheral: str = "saradc") -> None:
    """Press and release a ladder button, then let the UI settle."""
    pin = BUTTON[name]
    sim.inject_gpio(peripheral, pin, True)
    sim.run_for(PRESS_SECONDS)
    sim.inject_gpio(peripheral, pin, False)
    sim.run_for(settle)


def heap_sample(sim) -> dict:
    """Engine-side heap view. Empty when the engine cannot read the allocator.

    On the Renode ESP32-C3 backend this returns None/{} -- the engine does not
    recognise ESP-IDF's multi_heap. Callers must treat {} as "unknown", never as
    zero, and fall back to `heap_from_log()`.
    """
    return sim.heap() or {}


#: `[BLELC] ... heap=118200 maxAlloc=106484` and `[MEM] Free: N ... MaxAlloc: N`.
_HEAP_KV = re.compile(r"heap=(\d+)\s+maxAlloc=(\d+)")
_HEAP_MEM = re.compile(r"\[MEM\] Free: (\d+).*?MaxAlloc: (\d+)")


def heap_from_log(lines) -> list[dict]:
    """Free bytes and largest block as the FIRMWARE sees them, from ring lines.

    Preferred over `heap_sample()` here: these are the exact values the start
    gates in `main.cpp` compare against, so a discrepancy between them and the
    engine's view would be a model question, not a firmware one. `maxAlloc` is
    the largest contiguous block -- the number fragmentation moves and a
    free-heap threshold cannot see.
    """
    out = []
    for line in lines:
        m = _HEAP_KV.search(line) or _HEAP_MEM.search(line)
        if m:
            free, largest = int(m.group(1)), int(m.group(2))
            out.append({"free": free, "maxAlloc": largest,
                        "gapBytes": free - largest, "line": line})
    return out


# --- driving the UI by observed state, not by fitted delays ----------------
#
# The firmware announces every screen change (`[ACT] Entering activity: Home`),
# so the bench can wait for the UI to arrive instead of guessing how long it
# takes. That matters beyond tidiness: a settle time tuned until a bench passed
# is calibrated against whatever else was broken at the time, and silently
# stops meaning anything when the firmware gets faster or slower.

ACTIVITY_ENTER = re.compile(r"\[ACT\] Entering activity: (\S+)")


class Wedged(AssertionError):
    """`logHead` stopped advancing -- the firmware is not logging any more."""


def pump(sim, ring: "Ring", seconds: float, slice_s: float = 0.25) -> list[str]:
    """Advance `seconds` of virtual time, collecting ring lines as they appear."""
    seen: list[str] = []
    for _ in range(max(1, int(seconds / slice_s))):
        sim.run_for(slice_s)
        seen.extend(ring.new(sim))
    return seen


def wait_for(sim, ring: "Ring", pattern: str, timeout_s: float = 20.0,
             slice_s: float = 0.25) -> list[str]:
    """Run until a ring line matches `pattern`; TimeoutError if it never does.

    Returns every line seen while waiting, so callers can assert on what else
    happened on the way.
    """
    rx = re.compile(pattern)
    seen: list[str] = []
    for _ in range(max(1, int(timeout_s / slice_s))):
        sim.run_for(slice_s)
        fresh = ring.new(sim)
        seen.extend(fresh)
        if any(rx.search(l) for l in fresh):
            return seen
    raise TimeoutError(f"no ring line matched {pattern!r} in {timeout_s}s of "
                       f"virtual time; last lines: {seen[-6:]}")


def press_until(sim, ring: "Ring", button: str, pattern: str, max_presses: int = 20,
                settle_s: float = 1.5) -> list[str]:
    """Press `button` until a ring line matches, or give up loudly."""
    rx = re.compile(pattern)
    seen: list[str] = []
    for _ in range(max_presses):
        press(sim, button, 0.0)
        seen.extend(pump(sim, ring, settle_s))
        if any(rx.search(l) for l in seen):
            return seen
    raise TimeoutError(f"{max_presses} x {button!r} never produced {pattern!r}; "
                       f"last lines: {seen[-6:]}")


def assert_alive(sim, ring: "Ring", button: str = "back", timeout_s: float = 8.0) -> None:
    """Liveness: press a button and require the firmware to react.

    An idle CrossPoint logs nothing for long stretches -- it sits in `loop()`
    with nothing to say, and `[PWR] Going to low-power mode` is the normal end
    of a quiet period. So "no new log lines" is NOT evidence of a wedge; an
    earlier version of this helper asserted exactly that and produced false
    failures. Liveness has to be probed: give the device an input and require a
    response.
    """
    before = ring.head_index(sim)
    press(sim, button, 0.0)
    for _ in range(max(1, int(timeout_s / 0.25))):
        sim.run_for(0.25)
        if ring.head_index(sim) != before:
            return
    raise Wedged(
        f"no log activity within {timeout_s}s of pressing {button!r} "
        f"(logHead stayed {before}) -- firmware is not responding to input; "
        f"last lines: {ring.lines(sim)[-4:]}"
    )


def saw_reboot(lines) -> bool:
    """True if `lines` contain evidence of a restart (e.g. SilentRestart-to-defrag).

    The ring buffer lives in RTC_NOINIT memory precisely so it survives a reset,
    so a reboot appears as boot-time lines reappearing, not as a gap.
    """
    return any("Xteink probe scores" in l or "silent restart" in l.lower()
               for l in lines)


def press_and_wait_for_repaint(sim, ring: "Ring", button: str,
                               timeout_s: float = 6.0) -> list[str]:
    """Press a button and wait until the panel reports it finished repainting.

    The X4 is e-ink: the firmware's own `[GFX] Time = N ms` lines show full
    refreshes taking ~1700 ms. Pressing again before that lands mid-repaint and
    the input is dropped -- which silently corrupts any "press down N times"
    navigation. Waiting for the repaint the firmware announces removes the
    guesswork instead of tuning a settle time until it happens to work.
    """
    press(sim, button, 0.0)
    try:
        return wait_for(sim, ring, r"\[GFX\] Time = \d+ ms", timeout_s=timeout_s)
    except TimeoutError:
        # No repaint reported: either the press changed nothing on screen, or
        # it was dropped. The caller cannot tell these apart, so say so.
        return []


def activate_menu_row(sim, ring: "Ring", n_down: int, settle_s: float = 3.0) -> list[str]:
    """From a freshly-opened menu, move down `n_down` rows and activate one.

    Selection movement is NOT logged -- only activation is -- so a bench cannot
    watch the highlight travel and stop on the row it wants. It has to move a
    known number of steps and then commit, which makes it essential that every
    step actually registers; see `press_and_wait_for_repaint`.
    """
    seen: list[str] = []
    for _ in range(n_down):
        seen.extend(press_and_wait_for_repaint(sim, ring, "down"))
    press(sim, "confirm", 0.0)
    seen.extend(pump(sim, ring, settle_s))
    return seen


def return_to_reader(sim, ring: "Ring", max_backs: int = 6) -> bool:
    """Press back until the reader is on screen again. True if it got there.

    Menu rows can open submenus or popups, so one `back` is not enough to
    restore a known state -- an earlier version of the row search assumed it
    was, and every probe after the first ran from an unknown screen.
    """
    for _ in range(max_backs):
        lines = ring.lines(sim)
        if any("Entering activity: EpubReader" in l and "Menu" not in l for l in lines[-6:]):
            return True
        press(sim, "back", 0.0)
        seen = pump(sim, ring, 1.5)
        if any("Entering activity: EpubReader" in l and "Menu" not in l for l in seen):
            return True
    return False


def map_reader_menu(sim, ring: "Ring", max_rows: int = 18) -> list[dict]:
    """Activate each reader-menu row in turn and record what it did.

    Returns one entry per row so the caller can find the row it wants by what
    the firmware SAID, not by an index guessed from source order (FOOTNOTES,
    BOOKMARKS and FRONTLIGHT are all conditional, so the index is not fixed).
    Rows that could not be reached are recorded as such rather than skipped
    silently.
    """
    out = []
    for n in range(max_rows):
        if not return_to_reader(sim, ring):
            out.append({"row": n, "reached": False, "lines": [], "note": "lost the reader"})
            break
        opened = False
        for _ in range(3):
            press(sim, "confirm", 0.0)
            if any("Entering activity: EpubReaderMenu" in l for l in pump(sim, ring, 1.5)):
                opened = True
                break
        if not opened:
            out.append({"row": n, "reached": False, "lines": [], "note": "menu did not open"})
            continue
        lines = activate_menu_row(sim, ring, n)
        interesting = [l for l in lines
                       if any(k in l for k in ("[ACT]", "[ERM]", "[BLELC]", "[BLEUI]", "silent restart"))]
        out.append({"row": n, "reached": True, "lines": lines, "summary": interesting[-3:]})
    return out


#: Rows whose activation produces BLE lifecycle output. TOGGLE_BLUETOOTH does
#: not push an activity, so it cannot be found by watching `[ACT]` lines.
BLE_ROW_SIGNATURE = r"(\[BLELC\]|\[ERM\].*BT |silent restart|Entering activity: BluetoothSettings)"


def find_bluetooth_row(sim, ring: "Ring", max_rows: int = 16):
    """Locate the reader menu's Bluetooth toggle by what it does, not by index.

    `buildMenuRowItems()` adds FOOTNOTES, BOOKMARKS and FRONTLIGHT
    conditionally, so TOGGLE_BLUETOOTH's position depends on the book and the
    board -- hard-coding it would pass on one fixture and silently mis-target on
    the next. Probing costs a few minutes of virtual time and stays correct.

    Returns (row_index, lines). Raises LookupError listing what each row did,
    which is the useful output when it fails.
    """
    rx = re.compile(BLE_ROW_SIGNATURE)
    tried = []
    for n in range(max_rows):
        if not return_to_reader(sim, ring):
            raise LookupError(f"lost the reader before probing row {n}; tried:\n"
                              + "\n".join(tried))
        opened = False
        for _ in range(3):
            press(sim, "confirm", 0.0)
            if any("Entering activity: EpubReaderMenu" in l for l in pump(sim, ring, 1.5)):
                opened = True
                break
        if not opened:
            tried.append(f"  row {n}: menu would not open")
            continue
        lines = activate_menu_row(sim, ring, n)
        if any(rx.search(l) for l in lines):
            return n, lines
        acts = [l for l in lines if "[ACT] Entering" in l]
        tried.append(f"  row {n}: {acts[-1].split('Entering activity: ')[-1] if acts else '(toggle, no activity)'}")
    raise LookupError("no reader-menu row produced BLE output:\n" + "\n".join(tried))
