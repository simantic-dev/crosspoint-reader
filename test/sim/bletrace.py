# Generic BLE PHY/MAC register-region tracer (store + read + log).
#
# Reconstructed for rundir-featbt: the original scripted helper referenced by
# ESP32C3_ble.replx (~/.claude/jobs/bd9fca3c/tmp/bletrace.py, per the
# infra-crosspoint/docs/esp32c3-ble-m3-continuous-scan.md BLE M1-M6 work) no
# longer exists on disk anywhere under ~/dev/simantic — it lived only in an
# ephemeral job-scratch directory. That doc's own repl comment describes the
# contract these blocks need exactly:
#
#   "Surrounding BLE/PHY register regions — scripted tracers (store+read+log)
#    so the controller/PHY blob doesn't hard-fault on unmapped access, and the
#    fault-loop can see what it polls. Promote to C# behaviour as spins are
#    found."
#
# So this is a plain per-offset storage (reads return the last write, unwritten
# offsets default to 0) with an access log at "noisy" level (filtered out by
# default; pass -v/whatever raises the sim's scripted-peripheral log level to
# see it) so a debugging session can still see what firmware polls without
# flooding a normal run. None of the four blocks that share this file
# (btmac @0x60011000, fe2 @0x60005000, fe @0x60006000, llcoex @0x60035000) are
# wired for interrupts here (numberOfInterrupts: 0 in the .replx) — they are
# pure "don't fault, just remember what was written" MMIO stand-ins, matching
# what M1-M6 of the RE doc landed as real behaviour in ESP32C3_Radio.cs
# instead (this file only covers the *neighbourhood* still un-modeled).


class Peripheral:
    def __init__(self, ctx):
        self.ctx = ctx
        self.storage = {}  # offset -> last-written value

    def on_access(self, req):
        if req.is_init:
            self.storage.clear()
            return
        if req.is_write:
            self.storage[req.offset] = req.value
            self.ctx.noisy("write off=0x%x val=0x%x len=%d" %
                            (req.offset, req.value, req.length))
        elif req.is_read:
            req.value = self.storage.get(req.offset, 0)
            self.ctx.noisy("read  off=0x%x val=0x%x len=%d" %
                            (req.offset, req.value, req.length))
