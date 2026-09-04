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

The UI walk is INPUT, not platform. ``JOURNEY`` below is the whole of it, and it
is replayed into the SAR-ADC ButtonMap from Python, so changing the walk is an
edit here rather than to a scripted board helper baked into a .replx. The board
helper that remains models only the board: the power-on hold and the VBUS
divider, neither of which a test should be pressing.

Run with:

    pip install simantic
    SIMANTIC_SIM=<publish-dir>/sim pytest test/sim/test_ble_page_turner.py -v
"""
import contextlib
import os
from pathlib import Path

import pytest

simantic = pytest.importorskip(
    "simantic", reason="pip install simantic to run the sim-backed tests")
from simantic import Sim  # noqa: E402  (after importorskip, by design)

HERE = Path(__file__).resolve().parent

#: The scenario's firmware. A build artifact that does not live in this repo;
#: the tests skip when it is absent rather than failing, so a bare checkout
#: still runs green. Build it with build_payload.sh -- and note that a payload
#: built against a truncated mask-ROM dump boots and runs the whole UI but
#: panics the instant Bluetooth is switched on (illegal instruction at
#: 0x40001c34, the ROM PHY jump table). Check that address is not zero:
#:     xxd -s $((0x94 + 0x1c34)) -l 4 <payload>   # must not be 00000000
#: $CROSSPOINT_SIM_PAYLOAD overrides it, so a payload built elsewhere can be
#: tested without copying it into the tree.
PAYLOAD = Path(os.environ.get("CROSSPOINT_SIM_PAYLOAD")
               or HERE.parent / "payloads" / "crosspoint-featbt-latest-sim.elf")

#: Ladder button -> SAR-ADC ButtonMap input, from the replx wiring. Injecting
#: into the ButtonMap is what the live control channel does for a human; doing
#: it here keeps the firmware's own input path in the test.
BUTTON = {"back": 0, "confirm": 1, "left": 2, "right": 3, "up": 4, "down": 5}

#: Short press. ButtonNavigator treats >=500 ms as a hold, which selects a
#: different action, so this must stay under it.
PRESS_SECONDS = 0.4

#: Virtual seconds from reset to the reader being on screen and taking input.
BOOT_SECONDS = 14

# Walking CrossPoint's own UI to the Bluetooth screen, as a person would: the
# reader menu, down to "Go Home", Settings, Controls, Bluetooth, then Scan.
# BLE does not exist before this point -- NimBLE is started by the Bluetooth
# screen and torn down on leaving it -- so a test that only boots the firmware
# and waits will sit on an idle screen until its timeout and report the silence
# as "the peer never advertised". That is precisely what the first run of this
# suite did.
#
# Settles are VIRTUAL seconds. They are e-ink repaint and menu-animation times,
# recorded against this build rather than tuned until the bench passed: an
# 800x480 mono panel takes 1-2s per full update, which is what every 1.5 below
# is paying for.
JOURNEY = [
    ("confirm", 3),                       # reader menu
    *[("down", 1.5)] * 9, ("down", 2.5),  # scroll to "Go Home"
    ("confirm", 6),                       # home
    *[("down", 1.5)] * 3, ("down", 2.5),  # scroll to Settings
    ("confirm", 5),                       # settings
    ("confirm", 2.5),
    ("confirm", 3),                       # controls
    ("up", 3),                            # bluetooth row
    ("confirm", 6),                       # bluetooth screen -- NimBLE starts here
    ("confirm", 6),                       # toggle Bluetooth ON
    ("down", 8),                          # stack up + repaint; move to Scan row
    ("confirm", 25),                      # start scan
    ("confirm", 10),                      # select the first result
]

#: Encoding -> the platform that loads that peer script. The two differ only in
#: the ScriptedBlePeer's `file:`; everything else about the board is identical.
ENCODINGS = {
    "keyboard": "board-featbt-scripted.replx",
    "bitmap": "board-featbt-scripted-bitmap.replx",
}

#: The X4's SD card. Like the payload, a build artifact rather than a repo file.
#: It is resolved by the engine against the PROCESS working directory, not
#: against the scenario or the platform file -- hence the chdir in the fixture.
SDCARD = HERE / "sdcard.img"

pytestmark = [
    pytest.mark.skipif(not PAYLOAD.exists(),
                       reason=f"CrossPoint payload not present at {PAYLOAD}"),
    pytest.mark.skipif(not SDCARD.exists(),
                       reason=f"SD card image not present at {SDCARD}"),
]


def _scenario(repl: str) -> dict:
    """The `sim --scenario` schema, as a dict.

    The peer rides on the xteink machine and the BLE medium joins its radio to
    the C3's, so one machine carries both ends of a real air interface.
    """
    return {
        "machines": {"xteink": {"repl": repl, "elf": str(PAYLOAD)}},
        "media": [{"type": "ble", "connect": ["xteink.radio", "xteink.blepeer"]}],
    }


def press(sim: "Sim", name: str, settle: float) -> None:
    """One short press, then the repaint it causes, in virtual time."""
    pin = BUTTON[name]
    sim.inject_gpio("saradc", pin, True)
    sim.run_for(PRESS_SECONDS)
    sim.inject_gpio("saradc", pin, False)
    sim.run_for(settle)


@pytest.fixture(scope="module", params=sorted(ENCODINGS))
def remote(request):
    """A running scenario, walked to the Bluetooth screen, and its peer.

    Module-scoped: the journey costs ~110 virtual seconds of menu navigation and
    e-ink repaints, and every assertion below is about the same run's outcome.
    Per-function scope would walk the whole UI once per assertion -- six journeys
    for three tests across two encodings -- for no extra coverage.
    """
    # chdir, because the SD card image is the one path the engine resolves
    # against the process working directory rather than against cwd= or the
    # platform file -- from anywhere else the machine fails to construct.
    with contextlib.chdir(HERE), \
            Sim(scenario=_scenario(ENCODINGS[request.param]),
                machine="xteink", uart="uart0", cwd=HERE) as sim:
        sim.run_for(BOOT_SECONDS)
        for name, settle in JOURNEY:
            press(sim, name, settle)
        yield request.param, sim


#: Advertising-channel access address (Core v6.3 Vol 6 Part B 2.1.2). A frame on
#: the medium is the access address followed by the channel PDU, so the PDU type
#: is the low nibble of the byte after it.
ADV_ACCESS_ADDRESS = bytes((0xD6, 0xBE, 0x89, 0x8E))
ADV_IND, SCAN_REQ, SCAN_RSP, CONNECT_IND = 0, 3, 4, 5


def _adv_pdus(sim, pdu_type: int) -> list[dict]:
    """Advertising-channel PDUs of one type seen on the medium.

    Decoded from the bytes rather than matched on a summary string: frames carry
    raw octets, and the PDU type is what identifies them.
    """
    out = []
    for f in sim.frames(from_start=True):
        data = f.get("data")
        if (f.get("protocol") or "").upper() != "BLE" or not data or len(data) < 6:
            continue
        if data[:4] != ADV_ACCESS_ADDRESS:
            continue  # a connection-event PDU: different access address
        if data[4] & 0x0F == pdu_type:
            out.append(f)
    return out


def _require_debug_build(sim) -> str:
    """The UART text, once it is established that this payload can speak.

    A positive control. The two assertions below read log lines that only exist
    when the payload is built with FREEINK_BLE_HID_SCAN_DEBUG /
    FREEINK_BLE_HID_REPORT_DEBUG, so on a release image a missing line means
    "not compiled in", not "did not happen" -- a failure that could not have
    come out any other way is not evidence. BleKeyboardHost::begin() prints
    "[BleHid] begin:" the moment Bluetooth is switched on, so its absence
    identifies the build rather than the behaviour, and this skips instead.
    """
    text = sim.read_uart(from_start=True)
    if "[BleHid]" not in text:
        pytest.skip("payload has no BLE debug logging compiled in "
                    "(FREEINK_BLE_HID_SCAN_DEBUG); these two assertions read "
                    "log lines and cannot tell silence from absence")
    return text


def test_remote_is_discovered_and_paired(remote):
    """Scan through bonding.

    Asserted on the air rather than on a log line: CrossPoint's scan and pairing
    prints are behind FREEINK_BLE_HID_SCAN_DEBUG, so a release payload would make
    a log-based assertion pass vacuously. A CONNECT_IND on the medium cannot.

    CrossPoint calls secureConnection() unconditionally, so there is no path where
    a HID remote connects without pairing: this covers the SMP exchange, the
    session key both ends derive, and the encrypted link GATT then runs over.
    """
    _, sim = remote
    assert _adv_pdus(sim, ADV_IND), "peer never advertised"
    assert _adv_pdus(sim, SCAN_REQ), "firmware scanned passively, never actively"
    assert _adv_pdus(sim, CONNECT_IND), "firmware never connected to the peer"


def test_report_map_is_read_and_reports_are_subscribed(remote):
    """The peer only sends a key after the firmware writes its report CCC, so a
    report arriving at all proves the firmware discovered the HID service, read
    the Report Map, and subscribed.

    The Report Map matters more than it looks: it is 49 octets for the keyboard
    peer and longer with the consumer collection the bitmap peer adds, so it
    cannot be read in a single ATT response at the default MTU. This assertion
    fails if Read Blob continuation regresses.

    Both encodings assert the same line, because the host logs every
    notification's bytes the same way regardless of shape -- the encodings
    diverge later, in pollBle(), which the next test covers.
    """
    _, sim = remote
    # BleKeyboardHost.cpp: Serial.printf("[BleHid] report len=%u %s\n", ...).
    # Gated behind FREEINK_BLE_HID_REPORT_DEBUG, so on a payload built without
    # it this assertion goes quiet rather than wrong -- unlike the pairing test
    # above, which reads the air and holds for a release build too.
    assert "[BleHid] report len=" in _require_debug_build(sim), \
        "no HID report arrived: the firmware never subscribed to the report CCC"


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
    encoding, sim = remote
    if encoding != "keyboard":
        pytest.skip("vendor bitmap codes are capture-and-assign by design")
    # pollBle() used to resolve keys silently, so there was nothing to assert on
    # -- a bare r"page|next" matches the UI's own "Page Turn" label and would
    # have passed without a single report arriving. The BLEIN line added
    # alongside these fixes states the resolution outcome explicitly.
    text = _require_debug_build(sim)
    assert "BLEIN" in text and "-> button" in text, \
        "no mapped BLE key reached the input layer"
    assert "unbound, dropped" not in text, \
        "the right-arrow was decoded but had no default binding"
