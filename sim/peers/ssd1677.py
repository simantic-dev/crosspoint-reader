# SSD1677 e-paper controller (800x480, 1 bpp BW plane) as a scripted SPI peer, vendored for this demo.
# Wiring: DC on input line 1 (low = command), BUSY on output line 2, chip select unused (one byte per
# transaction, as the firmware's EpdBus does). Keeps the BW RAM (0x24) and RED RAM (0x26) through the
# RAM window/counters (0x11, 0x44, 0x45, 0x4E, 0x4F, controller datasheet "RAM address" section) and, on
# MASTER_ACTIVATION (0x20) with 0x22 bit 2 (DISPLAY) set, writes the BW plane to <EPD_DUMP>-NNN.pbm.
# RAM bit 1 = white, PBM bit 1 = black, so the dump is inverted. Raw panel orientation (800 wide):
# convert with frame2png.py --transpose for the X4 as held (480x800).
# BUSY time is a declared model constant (1.6 s full refresh, 150 ms otherwise), not fitted.
# Control port: get property "state" ({"n": plain BW refreshes}) or "frame" ({"n", "w": 800, "h": 480, "bits":
# base64 of the BW plane at the last plain refresh, RAM layout, bit 1 = white}) on the bus peer's board name.
# A refresh after a custom waveform (0x32, CrossPoint's grayscale pass) does not replace the shown page.
# Not modelled: grayscale (custom LUT, RED plane composing), waveforms, the temperature/OTP commands.
import base64
import os

W, H = 800, 480
WB = W // 8
DC_LINE, BUSY_LINE = 1, 2
FULL_US, POWER_US = 1_600_000, 150_000


class Slave:
    def __init__(self, ctx):
        self.ctx = ctx
        self.dc = False
        self.cmd = 0
        self.n = 0              # data byte index within the command
        self.p = 0              # 16-bit parameter assembly
        self.mode = 0x03
        self.xs = self.xe = self.x = 0
        self.ys = self.ye = self.y = 0
        self.upd = 0xFF
        self.bw = bytearray(b"\xff" * (WB * H))
        self.red = bytearray(b"\xff" * (WB * H))
        self.lut = False        # a custom waveform (0x32) was written since the last activation
        self.frames = 0
        self.shown = bytes(self.bw)
        self.shown_n = 0        # plain BW refreshes: what "state"/"frame" report
        self.path = os.environ.get("EPD_DUMP", "sim/out/epd")

    def on_gpio(self, line, level):
        if line == DC_LINE:
            self.dc = bool(level)

    def on_transmit(self, v):
        if not self.dc:
            self.cmd, self.n = v, 0
            if v == 0x32:
                self.lut = True
            if v == 0x12:
                self.mode, self.upd = 3, 0xFF
            elif v == 0x20:
                self._activate()
            return 0
        c, n = self.cmd, self.n
        self.n += 1
        if c in (0x24, 0x26):
            if 0 <= self.y < H and 0 <= self.x < WB:
                (self.bw if c == 0x24 else self.red)[self.y * WB + self.x] = v
            self._advance()
        elif c == 0x22 and n == 0:
            self.upd = v
        elif c == 0x11:
            self.mode = v
        elif c in (0x44, 0x45, 0x4E, 0x4F):
            self.p = v if n % 2 == 0 else self.p | (v << 8)
            if n % 2 == 1:
                self._param(c, n)
        return 0

    def _param(self, c, n):
        p = self.p
        if c == 0x44:       # X window: pixel addresses, kept as byte columns
            if n == 1: self.xs = p >> 3
            else: self.xe = p >> 3
        elif c == 0x45:
            if n == 1: self.ys = p
            else: self.ye = p
        elif c == 0x4E and n == 1:
            self.x = p >> 3
        elif c == 0x4F and n == 1:
            self.y = p

    def _advance(self):
        xi, yi = self.mode & 1, self.mode & 2
        self.x += 1 if xi else -1
        if (self.x > self.xe) if xi else (self.x < self.xe):
            self.x = self.xs
            self.y += 1 if yi else -1
            if (self.y > self.ye) if yi else (self.y < self.ye):
                self.y = self.ys

    def _activate(self):
        display = bool(self.upd & 0x04)
        if display:
            self.frames += 1
            # A refresh with a custom waveform (0x32) is CrossPoint's grayscale pass: it drives the BW/RED planes
            # as an anti-aliasing mask over the page already on the glass. Grayscale is not modelled, so the
            # page as shown stays the last plain BW refresh.
            if not self.lut:
                self.shown = bytes(self.bw)
                self.shown_n += 1
            print("EPD frame %d at %.3f s upd=0x%02x lut=%d" % (self.frames, getattr(self.ctx, "now_us", 0) / 1e6, self.upd, self.lut), file=__import__("sys").stderr)
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            with open("%s-%03d.pbm" % (self.path, self.frames), "wb") as f:
                f.write(b"P4\n%d %d\n" % (W, H))
                f.write(bytes(b ^ 0xFF for b in self.bw))
        self.lut = False
        self.ctx.gpio(BUSY_LINE).set(True)
        self.ctx.schedule_oneshot(FULL_US if display else POWER_US, self.on_timer)

    def on_timer(self):
        self.ctx.gpio(BUSY_LINE).set(False)

    def get_property(self, name):
        if name == "state":
            return {"n": self.shown_n}
        if name == "frame":
            return {"n": self.shown_n, "w": W, "h": H, "bits": base64.b64encode(self.shown).decode()}
        raise KeyError(name)


if __name__ == "__main__":
    import tempfile

    class G:
        def set(self, v): pass

    class Ctx:
        def gpio(self, n): return G()
        def schedule_oneshot(self, us, cb): pass

    os.environ["EPD_DUMP"] = os.path.join(tempfile.mkdtemp(), "t")
    s = Slave(Ctx())

    def cmd(c, *d):
        s.on_gpio(DC_LINE, 0); s.on_transmit(c); s.on_gpio(DC_LINE, 1)
        for b in d: s.on_transmit(b)

    cmd(0x11, 0x01)                                  # X inc, Y dec
    cmd(0x44, 0, 0, 15, 0); cmd(0x45, 1, 0, 0, 0)    # window: 2 byte columns, rows 1..0
    cmd(0x4E, 0, 0); cmd(0x4F, 1, 0)
    cmd(0x24, 0x00, 0x0F, 0xAA, 0x55)                # row1: 00 0F, row0: AA 55
    cmd(0x22, 0xF7); cmd(0x20)
    d = open(s.path + "-001.pbm", "rb").read().split(b"\n", 2)[2]
    assert d[WB:WB + 2] == b"\xff\xf0" and d[0:2] == b"\x55\xaa", d[:4]
    print("ssd1677 selftest ok")
