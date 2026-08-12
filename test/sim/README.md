# BLE page-turner tests on simulated hardware

These tests run the real CrossPoint image on a simulated Xteink X4 (ESP32-C3) and
pair it with a firmware-less BLE HID page-turner over a simulated air interface.
Nothing is stubbed between the firmware and the link: the scan, the SMP exchange,
the session key, GATT discovery and the HID input reports are all real traffic
between two models.

They exist because the page-turner work in #2418 has failure modes that only show
up end to end, and that a person holding a remote cannot easily distinguish from
each other — "the remote does nothing" is the same symptom whether the key never
arrived, arrived and was dropped, or arrived at a screen with nothing to act on.

## What is covered

| Test | What breaks if it fails |
| --- | --- |
| `test_remote_is_discovered_and_paired` | Active scan across the SCAN_REQ/SCAN_RSP turnaround; SMP; the encrypted link |
| `test_report_map_is_read_and_reports_are_subscribed` | HID service discovery, Report Map read (too long for one ATT response — needs Read Blob continuation), report CCC subscribe |
| `test_arrow_key_turns_a_page_without_being_mapped` | The default bindings. Without them an unmapped remote is inert |

## The two remote encodings

Both are parameterised into every test, because they take different routes
through `BleKeyboardHost::onReportIngest` and only the first was ever exercised:

- **keyboard** — an 8-byte boot-protocol report carrying a HID usage. Takes the
  standard keyboard slot path and surfaces as a `SpecialKey`, so the default
  bindings apply and the key works with no mapping.
- **bitmap** — a button bitmap, the shape a Hanlinyue **Free 2** presents. These
  are *not* keyboard usages and *not* consumer usage IDs; they fall through to the
  generic fallback, where the identity is the first non-zero byte. Because such
  vendor codes carry no portable meaning, they stay capture-and-assign — the
  bitmap test pins the decode, not a default binding.

### Devices are declared, not copied

`hid_descriptor.py` builds a Report Map and the reports that conform to it from one
declaration, because that is how a host reads a device: Report Size and Report Count
give the bit geometry, and the Input item's Variable-vs-Array flag decides whether a
control is one bit of a bitfield or a usage code in an array slot
([kernel.org](https://docs.kernel.org/hid/hidintro.html)).

Writing it the other way round — copying the bytes one remote happens to emit —
fits the bench to that remote and says nothing about the next one. The Free 2's
`20 00 00` is not a magic number under this construction: it is bit 5 of a variable
input, i.e. **button 6** of a 24-bit button bitmap, and `04 00 00` is button 3.
Changing `PRESS_BUTTON` in the peer makes it a different remote without editing a
single report byte, and `button_bitfield`, `boot_keyboard` and `consumer_array`
cover the three encodings a page-turner can plausibly use.

`test_hid_descriptor.py` pins all of this and needs **no simulator, payload or
hardware** — it runs in a bare checkout.

The same remote emits different encodings in its other power-button-cycled modes,
which is exactly why the capture-then-assign design is right and why hardcoded
per-vendor profiles would be fragile.

## Running

The firmware payload and the board model are build/simulator artifacts that do
not live in this repo, so the tests **skip** in a bare checkout rather than fail.
To run them you need a simulator binary and a rundir containing
`board-featbt-hidpeer.replx` and its helper scripts:

```bash
pytest test/sim/test_ble_page_turner.py -v --sim <path-to-sim>
```

Every assertion reads the firmware's own log. UART capture block-buffers at 8 KB
and this journey emits well under that in 240 virtual seconds, so nothing reaches
the file the harness polls until the process exits. The tests therefore also skip
unless `SIMANTIC_UART_AUTOFLUSH=1` marks a simulator that flushes as it goes.

The scenario spends roughly 110 virtual seconds walking the firmware's own UI to
the Bluetooth screen before BLE matters. That is deliberate — it is the bench that
proves the whole stack — and it is not meant to run on every commit.
