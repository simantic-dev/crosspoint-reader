"""BLE HID and the reader competing for heap on the Xteink X4.

The page-turn happy path is settled. What is not settled -- and what a device
cannot easily be made to do on demand -- is what happens when the BLE host and
the EPUB reader want the same memory at the same time.

The firmware's own numbers frame the problem. NimBLE is ~57 KB resident. The
reader wants a 48 KB framebuffer plus section builds, image decodes and glyph
bitmaps, most of them large and contiguous. They do not both fit, so
`main.cpp` defers the BLE start behind four gates:

    activityManager.bluetoothStartDeferred()          # activity busy
    isReaderActivity() && !renderer.hasFrameBuffer()  # framebuffer lent out
    isReaderActivity() && RenderLock::peek()          # render in progress
    ESP.getFreeHeap() < startFloor                    # 80 KB, or 70 KB explicit

and the reader can evict BLE the other way (`EpubReaderActivity.cpp`, which
records "aborted at maxAlloc ~11 KB with BLE resident").

Two properties make this worth simulating rather than bench-testing:

* **Failure is not graceful.** CrossPoint builds with `-fno-exceptions`, so a
  failed allocation is a hard abort, not a caught error (merged PR #2526). A
  mis-negotiation reboots the device mid-page rather than showing an error.
* **The number that decides it is fragmentation, not free heap.** A free-heap
  threshold is precisely what fragmentation defeats, which is why upstream has
  a commit titled "Fix JPEG/PNG decoder heap checks for fragmented memory".

So these tests assert on *largest free block* alongside free bytes, and treat
"deferred" as the correct outcome and "aborted" as the failure -- the firmware
is allowed to refuse, it is not allowed to die.
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path

import pytest

simantic = pytest.importorskip("simantic", reason="pip install simantic to run the sim benches")
from simantic import Sim  # noqa: E402

import simbench  # noqa: E402
from simbench import (Ring, assert_alive, heap_from_log, heap_sample, press,  # noqa: E402
                      find_bluetooth_row, press_until, pump, saw_reboot,  # noqa: E402
                      wait_for)

HERE = Path(__file__).resolve().parent
PAYLOAD = Path(os.environ.get("CROSSPOINT_SIM_PAYLOAD")
               or HERE.parent / "payloads" / "crosspoint-featble-sim.elf")
#: Symbol table for the same build. The payload itself is stripped.
FIRMWARE_ELF = Path(os.environ.get("CROSSPOINT_SIM_FIRMWARE_ELF")
                    or HERE.parent.parent / ".pio" / "build" / "sim" / "firmware.elf")
REPLX = "board-featbt-scripted.replx"
SDCARD = HERE / "sdcard.img"

BOOT_SECONDS = 6.0  # boot reaches loop() at ~3.0s; this is margin, not a fitted value

pytestmark = [
    pytest.mark.skipif(not PAYLOAD.exists(), reason=f"no payload at {PAYLOAD}"),
    pytest.mark.skipif(not SDCARD.exists(), reason=f"no SD image at {SDCARD}"),
]


def _scenario() -> dict:
    return {
        "machines": {"xteink": {"repl": REPLX, "elf": str(PAYLOAD)}},
        "media": [{"type": "ble", "connect": ["xteink.radio", "xteink.blepeer"]}],
    }


@pytest.fixture(scope="module")
def booted():
    """A booted X4 sitting on Home, with a ring-buffer reader attached."""
    simbench.assert_good_rom(PAYLOAD)
    elf = str(FIRMWARE_ELF) if FIRMWARE_ELF.exists() else None
    # The SD image is resolved against the process cwd, not the generated repl.
    with contextlib.chdir(HERE), Sim(scenario=_scenario(), machine="xteink",
                                     uart="uart0", cwd=HERE) as sim:
        ring = Ring(elf)
        sim.run_for(BOOT_SECONDS)
        if not ring.lines(sim):
            pytest.fail(
                "ring buffer empty after boot -- either the firmware never logged "
                "(check ENABLE_SERIAL_LOG/LOG_LEVEL in [env:sim]) or the ring "
                "addresses are wrong for this build. This is a harness failure, "
                "not a firmware verdict."
            )
        yield sim, ring


def _heap_line(sim) -> str:
    h = heap_sample(sim)
    if not h:
        return "heap=<engine did not recognise the allocator>"
    return (f"free={h.get('freeBytes')} largestBlock={h.get('largestFreeBlockBytes')} "
            f"frag={h.get('fragmentationRatio')}")


def test_the_reader_owns_the_framebuffer_at_boot(booted):
    """Positive control: the coexistence precondition actually holds.

    feat-ble resumes into the last book, so the reader is rendering before BLE
    is ever asked for -- which is the state we want to enable BLE from. If this
    fails, the tests below are exercising an idle menu, not coexistence, and
    their results mean nothing.
    """
    sim, ring = booted
    seen = wait_for(sim, ring, r"\[ERS\] Rendered page|\[SCT\] Page \d+ processed",
                    timeout_s=30)
    assert seen, "reader never rendered a page"


def test_enabling_bluetooth_while_reading_never_aborts(booted):
    """Toggle BT from the reader menu with a book open; it may refuse, not die.

    This is coexistence proper: feat-ble resumes into the reader, so the
    framebuffer is already owned and gates 2 and 3 in `main.cpp` are live when
    BLE is asked for.

    Three outcomes are all acceptable, and which one occurs is the finding:

      * BLE starts             -- `[BLELC] started ...`
      * the start is deferred  -- `[BLELC] start deferred: ...`
      * the device silently restarts to defragment
        -- `[ERM] BT enabled below heap floor (N); silent restart to defrag`

    The third is a reboot mid-book from the reader's point of view, so it is
    reported explicitly rather than passing in silence. What is NOT acceptable
    is a panic or an unresponsive device: `-fno-exceptions` turns a failed
    allocation into an abort (merged PR #2526), so a mis-negotiation kills the
    device outright rather than degrading.
    """
    sim, ring = booted
    row, seen = find_bluetooth_row(sim, ring)

    text = "\n".join(seen)
    assert "Guru Meditation" not in text, f"guest panicked enabling BT:\n{text}"
    assert "abort()" not in text, f"guest aborted enabling BT:\n{text}"

    samples = heap_from_log(seen)
    print(f"\n=== TOGGLE_BLUETOOTH found at reader-menu row {row} ===")
    for line in seen:
        if any(k in line for k in ("BLELC", "ERM", "silent restart", "BLEUI")):
            print("   ", line)
    print("   --- heap across the toggle (firmware's own numbers) ---")
    for s_ in samples:
        print(f"   free={s_['free']:>7}  maxAlloc={s_['maxAlloc']:>7}  "
              f"fragGap={s_['gapBytes']:>6}")
    if samples:
        first, last = samples[0], samples[-1]
        print(f"   BLE cost: {first['free'] - last['free']} bytes of free heap, "
              f"{first['maxAlloc'] - last['maxAlloc']} bytes of largest block")
    if saw_reboot(seen):
        print("   NOTE: SilentRestart-to-defrag path taken (device rebooted)")

    # It may refuse. It may not stop responding.
    assert_alive(sim, ring)


def test_fragmentation_is_visible_to_the_bench(booted):
    """The bench must be able to see largest-block, not just free bytes.

    Guards the harness, not the firmware. `sim.heap()` returns nothing on this
    backend (the engine does not recognise ESP-IDF's multi_heap), so we read the
    firmware's own `heap=`/`maxAlloc=` pairs -- which are the exact values
    `main.cpp`'s start gates compare against. If neither source works we must
    say so rather than report a green run built on absent data.
    """
    sim, ring = booted
    seen = pump(sim, ring, 20.0)
    samples = heap_from_log(seen)
    if not samples and not heap_sample(sim):
        pytest.skip("no heap numbers from either the engine or the firmware logs "
                    "in this window; heap assertions cannot be made")
    for s_ in samples:
        assert s_["maxAlloc"] <= s_["free"], (
            f"largest block exceeds free heap, report is inconsistent: {s_}")
    print("\n=== heap as the firmware sees it ===")
    for s_ in samples[-8:]:
        print(f"   free={s_['free']:>7}  maxAlloc={s_['maxAlloc']:>7}  "
              f"gap={s_['gapBytes']:>6}")
