# I2C0-region stub + power-button actor for the Xteink X4 (scripted, no C#).
#
# Two jobs in one MMIO block:
#
# 1. I2C0 stub (0x6001_3000): the X4 has no I2C devices; the firmware's X3
#    hardware probe pokes this block (~1.7k accesses) and must keep failing
#    fast exactly as it does against unmapped memory. Reads return 0, writes
#    are ignored — same semantics, minus the unhandled-access warning churn.
#
# 2. Power button (GPIO3, active-low): a cold POWERON boot with no USB maps
#    to WakeupReason::PowerButton, and CrossPoint's verifyPowerButtonWakeup
#    requires the button HELD for the configured duration — then released —
#    or it goes straight to deep sleep. Model the human turning the device
#    on: press at the firmware's first access here (well before the verify),
#    release after HOLD_US of virtual time.
#
#    irq(0) drives the button line: set() = HIGH = released,
#    clear() = LOW = pressed. The initial set() marks the pin as externally
#    driven in ESP32C3_GPIO before the clear() lands the press.
#
# Timer quirk: timers armed from __init__ are lost (the CLI boot flow never
# delivers an init access), so everything arms on the FIRST firmware access
# to this block, which the X3 probe guarantees happens pre-verify.
#
# Scripted UI sequences (recorded captures) are NOT this file's job any
# more: capture_states.py generates its own helper with a press timeline,
# and live runs (--control-port) inject presses through the SAR-ADC
# ButtonMap directly. This helper only models the human power-on.
#
#   i2c0: Scripted.ScriptedPeripheral @ sysbus 0x60013000
#       size: 0x1000
#       numberOfInterrupts: 8
#       file: "<path>/xteink_board_helper_boot.py"
#       0 -> gpio@3
#       1 -> saradc@0
#       ...
#       6 -> saradc@5
#       7 -> gpio@20
POWER = 0

# irqs 1..6 are the ladder buttons (saradc ButtonMap 0..5). This helper never
# drives them -- the test does, through the SAR-ADC directly -- so they are
# wired in the replx but unnamed here.


# GPIO20 doubles as the X4's USB/VBUS detect (BoardConfig usbDetect, new in the
# 2026-08 freeink-sdk) AND the X3 fingerprint's I2C SDA. On hardware the weak
# VBUS divider reads LOW as a plain input when USB is unplugged, yet the I2C
# probe's stronger pull-up rides above it while probing. Undriven in sim the pin
# keeps the probe's leftover pull-up -> reads HIGH -> getWakeupReason() =
# AfterUSBPower -> instant deep sleep on every cold boot; driving it low from
# boot instead wedges the I2C probe on a stuck-low SDA.
#
# MODELED SIMPLIFICATION: the divider-vs-pullup analog interplay isn't modeled.
# We present the post-probe level only: drive LOW on a timer that lands after
# the last fingerprint transaction (~arm+54 ms) and before the firmware's first
# usbDetect read (~arm+115 ms).
USB_DETECT = 7


class Peripheral:
    # Power press-to-release. Long enough for verifyPowerButtonWakeup's
    # calibrated duration (default 1 s, measured from boot), but MUST end
    # before loop()'s long-hold-to-sleep check arms (allowSleepAt = first
    # paint + 2 s): the 2026-08 firmware reaches loop() with a 5 s hold
    # still pressed and reads it as "user held power to sleep".
    HOLD_US = 2_000_000

    # (delay_us_after_previous, line, press?).
    STEPS = [
        (80_000, USB_DETECT, True),          # VBUS divider shows through, post-probe
        (HOLD_US - 80_000, POWER, False),    # release power (verify window passed)
    ]

    def __init__(self, ctx):
        self.ctx = ctx
        self.armed = False
        self.step = 0

    def on_access(self, req):
        if req.is_init:
            self.armed = False
            self.step = 0
            return
        if not self.armed:
            self.armed = True
            self.ctx.irq(POWER).set()    # mark line driven (glitch-high, pre-verify)
            self.ctx.irq(POWER).clear()  # press
            self.ctx.info("power button pressed")
            self.ctx.schedule_oneshot(self.STEPS[0][0])
        if req.is_read:
            req.value = 0

    def on_timer(self):
        _, line, press = self.STEPS[self.step]
        if line == POWER:
            # Power is active-low: released = high.
            self.ctx.irq(POWER).clear() if press else self.ctx.irq(POWER).set()
        elif line == USB_DETECT:
            # press=True = divider visible (LOW, USB unplugged). Glitch-set
            # first so the GPIO marks the pin externally driven.
            if press:
                self.ctx.irq(USB_DETECT).set()
                self.ctx.irq(USB_DETECT).clear()
            else:
                self.ctx.irq(USB_DETECT).set()
        else:
            # Ladder buttons are active-high into the SAR-ADC ButtonMap.
            self.ctx.irq(line).set() if press else self.ctx.irq(line).clear()
        self.ctx.info("step %d: line %d %s" % (self.step, line, "press" if press else "release"))
        self.step += 1
        if self.step < len(self.STEPS):
            self.ctx.schedule_oneshot(self.STEPS[self.step][0])
