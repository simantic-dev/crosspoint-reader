"""BLE page-turner regression tests, run against simulated hardware.

These exercise the paths that broke in the field on crosspoint-reader#2418, using
a firmware-less BLE HID peer on the other end of a simulated air interface. The
firmware under test is the real image; nothing is stubbed between it and the link.

Two remote encodings are covered, because they take different routes through
BleKeyboardHost::onReportIngest and only one of them was ever exercised:

  * ``keyboard`` -- an 8-byte boot-protocol report carrying a HID usage. Decodes
    through the standard keyboard slot path and arrives as a SpecialKey, so the
    default bindings apply and the key works without any mapping.
  * ``bitmap``   -- the 3-byte report a Hanlinyue "Free 2" actually emits (top
    ``20 00 00``, bottom ``04 00 00``, release ``00 00 00``), reverse-engineered
    by a tester on #2418. Not keyboard usages and not consumer usage IDs: it
    reaches the generic fallback, where the identity is the first non-zero byte.
    Vendor codes like these carry no portable meaning, so they stay
    capture-and-assign -- this test pins the decode, not a default binding.

Run with:

    pytest test/sim/test_ble_page_turner.py -v --sim <path-to-sim>
"""
import os
from pathlib import Path

import pytest
import yaml

HERE = Path(__file__).resolve().parent

# The journey walks the firmware's own UI to the Bluetooth screen before BLE
# matters, which costs ~110s of virtual time in menu navigation and e-ink
# repaints. Generous by design: this is the bench that proves the whole stack,
# not a per-commit test.
JOURNEY_TIMEOUT = 240

ENCODINGS = ["keyboard", "bitmap"]


def _scenario(encoding: str) -> Path:
    return HERE / f"xteink_pageturner_{encoding}.yaml"


def _payload(encoding: str = "keyboard") -> Path:
    """The scenario's firmware, or a path that does not exist in a bare checkout."""
    spec = yaml.safe_load(_scenario(encoding).read_text())
    return Path(spec["machines"]["xteink"]["elf"])


pytestmark = [
    pytest.mark.skipif(
        not _payload().exists(),
        reason=f"CrossPoint payload not present at {_payload()}",
    ),
    # Every assertion here reads the firmware's own log, and UART capture
    # block-buffers at 8KB: this journey emits under 4KB in 240 virtual seconds,
    # so nothing reaches the file the harness polls until the process exits --
    # expect() cannot see output that is still sitting in the buffer. Drop this
    # once the simulator's UART autoflush lands.
    pytest.mark.skipif(
        os.environ.get("SIMANTIC_UART_AUTOFLUSH") != "1",
        reason="needs UART autoflush; set SIMANTIC_UART_AUTOFLUSH=1 to run "
               "against a simulator that has it",
    ),
]


@pytest.fixture(params=ENCODINGS)
def remote(request, sim):
    """A running scenario and its peer, once per remote encoding."""
    scenario = sim.start_scenario(_scenario(request.param),
                                  virtual_timeout=JOURNEY_TIMEOUT)
    xteink = scenario["xteink"]
    return request.param, xteink, xteink.peripheral("blepeer")


def test_remote_is_discovered_and_paired(remote):
    """Scan through bonding.

    CrossPoint calls secureConnection() unconditionally, so there is no path where
    a HID remote connects without pairing: this covers the SMP exchange, the
    session key both ends derive, and the encrypted link GATT then runs over.
    """
    _, xteink, _ = remote
    xteink.uart.expect(r"\[BLE adv\].*PageTurner", timeout=JOURNEY_TIMEOUT)
    xteink.uart.expect(r"connected .*PageTurner|onLinkUp|paired", timeout=JOURNEY_TIMEOUT)


def test_report_map_is_read_and_reports_are_subscribed(remote):
    """The peer only sends a key after the firmware writes its report CCC, so a
    report arriving at all proves the firmware discovered the HID service, read
    the Report Map, and subscribed.

    The Report Map matters more than it looks: it is 49 octets for the keyboard
    peer and longer with the consumer collection the bitmap peer adds, so it
    cannot be read in a single ATT response at the default MTU. This assertion
    fails if Read Blob continuation regresses.
    """
    encoding, xteink, _ = remote
    expected = "bitmap report" if encoding == "bitmap" else "HID report"
    xteink.uart.expect(expected, timeout=JOURNEY_TIMEOUT)


def test_arrow_key_turns_a_page_without_being_mapped(remote):
    """The regression this suite exists for.

    A remote that has never been through Settings -> Bluetooth -> Map Remote
    Buttons used to be inert: pollBle() decoded the key correctly, found no entry
    in an empty bleKeyMap, and dropped it. Standard navigation keys now have
    default bindings, so a right-arrow turns a page out of the box.

    Only the keyboard peer is expected to page without mapping. The bitmap
    remote's ``0x20`` is a vendor code with no portable meaning -- deliberately
    left to capture-and-assign -- so it must reach the firmware (asserted above)
    but must not silently acquire a default binding.
    """
    encoding, xteink, _ = remote
    if encoding != "keyboard":
        pytest.skip("vendor bitmap codes are capture-and-assign by design")
    xteink.uart.expect(r"page|next", timeout=JOURNEY_TIMEOUT)
