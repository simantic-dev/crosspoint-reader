## This fork: CrossPoint booting in a simulator (partial)

Simantic's fork of CrossPoint, kept as a simulation showcase. The `sim/` directory runs the real firmware
(`default` env, ESP32-C3, Xteink X4/X3 binary) on a simulated ESP32-C3. Nothing here is offered back or posted
upstream. The firmware sources are unchanged; there are no firmware edits.

**Status: boot only. No e-paper screenshots yet.** The panel and the SD card are not wired in this run, so no
screen is drawn and no PNG exists. The next step is a Python SSD1677 peer that stores the BW RAM and dumps it per
refresh, and a SPI-mode SD peer carrying a card image (see "Gaps").

```
sim/run.sh, 4 virtual s, no board file (excerpt of sim/out/uart.log, console is mirrored on uart0 and usj)
  [0.827690s] [827] [INF] [HW] Xteink probe scores: pass1=0 pass2=0 verdict=0
  [0.827690s] [XTDET] NVS hw_calib/screenType: not set
  [0.895434s] [895] [XTDET] bus probe VER=FF FF FF FF FF FLG=FF -> default controller
  [2.947693s] [DBG] [SDFS] SD font system ready (0 families discovered)
  [2.947785s] [DBG] [ACT] Entering activity: FullScreenMessage
```

**What runs** (`sim` 0.10.0, build f219eb31, 2026-11-16; fork `develop` at 5c37b123):

| Part | In the simulation |
| --- | --- |
| Firmware | `pio run -e default`, flash image `firmware.factory.bin` (bootloader, partitions, app), booted through the built-in C3 mask ROM |
| ESP32-C3 | released `sim ESP32-C3` model, no board file |
| Panel, SD card, buttons, I2C parts | not present |

**Configurations tried** (4 s virtual each unless noted):

| Configuration | Where | Wall | Result |
| --- | --- | --- | --- |
| `default` env image, no board file | `sim/run.sh` | 9.4 s (load average 27) | runs: ROM banner, bootloader, app; X3 I2C probe fails on the unwired bus (`ESP_ERR_INVALID_STATE`), the X4 branch is taken, `FullScreenMessage` activity entered at 2.948 s |
| `env:sim` (UART0 console, `ARDUINO_USB_CDC_ON_BOOT=0`) | not kept | n/a | does not build: `ARDUINO_USB_CDC_ON_BOOT` redefined (`-Werror`) in managed components |
| Display, SD and button wiring | not built | n/a | `unverified`: not run |

`sim/build.sh` builds the unmodified `default` env. Build traps found: PlatformIO Core 6.1.18 is refused by the
pioarduino platform; Core 6.2.0 failed with `No module named 'SCons.Tool.FortranCommon'`; with 6.1.19 the first
link attempt failed with the same SCons message and a re-run succeeded. `freeink-sdk` is a submodule and must be
initialised (`sim/build.sh` does it).

**Gaps, with the evidence:**
- No display output: nothing models the SSD1677 in this run. A peer exists only as a BUSY/command-timing model in
  `sim-fixtures` (`peers/spi/ssd1677.py`; it states "Not modelled: ... frame output"). Wiring pins from the SDK X4
  profile: SCLK 8, MOSI 10, EPD CS 21, DC 4, RST 5, BUSY 6, SD MISO 7, SD CS 12, buttons on ADC1/ADC2, power GPIO3.
- Buttons are an ADC resistor ladder; whether this `sim` can change ADC pad voltage during a run was not tested:
  `unverified`.
- SD and EPD share one SPI bus; the pioneer-style `SPI.SPIMultiplexer` with CS pads was not tried: `unverified`.
- The build needed 8.7 GB of PlatformIO toolchains and libraries in a scratch core dir; not kept.

**Licence.** `LICENSE` is MIT ("covers original CrossPoint code, except where a file states other terms";
third-party code keeps its own licences; the combined firmware executable is GPLv3 because it includes wolfSSL;
see `licenses/README.md`). GitHub reports the key as `other` because of that addendum.
