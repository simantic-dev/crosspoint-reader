# MODELED SIMPLIFICATION: one scripted peer standing in for the whole Xteink X4 SPI2 bus (SD card + SSD1677 panel),
# attached directly to spi2. The released SPI.SPIMultiplexer delivers no bytes to its children on the ESP32-C3
# (see README), so the two chip selects are read as pad levels instead and each byte goes to the selected part.
# Lines: 1 = DC (GPIO4), 2 = BUSY out (GPIO6), 3 = EPD CS (GPIO21), 4 = SD CS (GPIO12); CS active low.
# Sim-side wiring only: both parts are the unmodified peers next to this file.
import importlib.util
import os

HERE = "sim/peers"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, name + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class Slave:
    def __init__(self, ctx):
        self.epd = _load("ssd1677").Slave(ctx)
        self.sd = _load("sd_spi").Slave(ctx)
        self.epd_cs = self.sd_cs = True          # both idle high

    def on_gpio(self, line, level):
        if line == 1:
            self.epd.on_gpio(1, level)
        elif line == 3:
            self.epd_cs = bool(level)
        elif line == 4:
            was, self.sd_cs = self.sd_cs, bool(level)
            if was and not self.sd_cs:
                self.sd.on_select()
            elif not was and self.sd_cs:
                self.sd.on_deselect()

    def get_property(self, name):
        return self.epd.get_property(name)

    def on_select(self):
        pass

    def on_deselect(self):
        pass

    def on_finish(self):
        pass

    def on_transmit(self, b):
        if not self.sd_cs:
            return self.sd.on_transmit(b)
        if not self.epd_cs:
            return self.epd.on_transmit(b)
        return 0xFF
