"""Build HID report descriptors, and the reports that conform to them.

A HID host derives a report's layout from the device's report descriptor: Report
Size and Report Count give the bit geometry, and the Input item's Variable-vs-Array
flag decides whether a control is one bit of a bitfield or a usage code placed in an
array slot. See the Linux kernel's introduction to report descriptors:
https://docs.kernel.org/hid/hidintro.html

So a bench for HID remotes should be written the same way round: declare the
descriptor, and let the report bytes fall out of it. The alternative -- copying the
bytes one remote happens to emit and hardcoding them -- fits the bench to that one
device and tells you nothing about the next one.

That distinction is not academic here. A Hanlinyue "Free 2" page-turner emits
``20 00 00`` for its top button. Written as a constant, 0x20 is a magic number
lifted from a teardown. Declared as a descriptor, it is simply bit 5 of a variable
input -- button 6 of a button bitfield -- and the same declaration expresses any
other bitfield remote by changing which button is pressed, not which byte is sent.

This module has no simulator or hardware dependency, so its self-tests run anywhere.
"""

# --- HID short items (USB HID 1.11 sec 6.2.2.2: bTag << 4 | bType << 2 | bSize) ---
USAGE_PAGE = 0x05
USAGE = 0x09
USAGE_MIN = 0x19
USAGE_MAX = 0x29
LOGICAL_MIN = 0x15
LOGICAL_MAX = 0x25
REPORT_SIZE = 0x75
REPORT_COUNT = 0x95
REPORT_ID = 0x85
INPUT = 0x81
COLLECTION = 0xA1
END_COLLECTION = 0xC0

# Input item flags (sec 6.2.2.5). Bit 1 selects Variable (a bitfield of independent
# controls) over Array (slots holding the usage code of whatever is active).
INPUT_ARRAY = 0x00      # Data, Array, Absolute
INPUT_VARIABLE = 0x02   # Data, Variable, Absolute
INPUT_CONSTANT = 0x01   # Constant -- padding, carries no control

PAGE_GENERIC_DESKTOP = 0x01
PAGE_KEYBOARD = 0x07
PAGE_BUTTON = 0x09
PAGE_CONSUMER = 0x0C

USAGE_KEYBOARD = 0x06
USAGE_CONSUMER_CONTROL = 0x01


def _item(tag, value=None, size=1):
    """One short item. size 0 emits the tag alone (e.g. End Collection)."""
    if value is None:
        return bytes([tag])
    if size == 1:
        return bytes([tag | 0x01, value & 0xFF])
    return bytes([tag | 0x02, value & 0xFF, (value >> 8) & 0xFF])


class Field:
    """One Input item: a run of `count` controls, each `size` bits wide.

    variable=True  -> each usage owns one bit position (a button bitmap)
    variable=False -> the run is an array of slots holding active usage codes
                      (how boot-protocol keyboards report keys)
    """

    def __init__(self, page, usage_min, usage_max, count, size=1, variable=True,
                 logical_max=None):
        self.page = page
        self.usage_min = usage_min
        self.usage_max = usage_max
        self.count = count
        self.size = size
        self.variable = variable
        self.logical_max = logical_max if logical_max is not None else (
            1 if variable else usage_max)

    @property
    def bits(self):
        return self.count * self.size

    def descriptor(self):
        return (_item(USAGE_PAGE, self.page)
                + _item(USAGE_MIN, self.usage_min)
                + _item(USAGE_MAX, self.usage_max)
                + _item(LOGICAL_MIN, 0)
                + _item(LOGICAL_MAX, self.logical_max)
                + _item(REPORT_SIZE, self.size)
                + _item(REPORT_COUNT, self.count)
                + _item(INPUT, INPUT_VARIABLE if self.variable else INPUT_ARRAY))


class Padding:
    """Constant bits inserted to reach a byte boundary. Carries no control."""

    def __init__(self, bits):
        self.bits = bits

    def descriptor(self):
        return (_item(REPORT_SIZE, self.bits)
                + _item(REPORT_COUNT, 1)
                + _item(INPUT, INPUT_CONSTANT))


class Device:
    """A HID device: one application collection over an ordered list of fields."""

    def __init__(self, name, page, usage, fields, report_id=None):
        self.name = name
        self.page = page
        self.usage = usage
        self.fields = fields
        self.report_id = report_id
        if sum(f.bits for f in fields) % 8:
            raise ValueError(f"{name}: reports must be whole bytes; got "
                             f"{sum(f.bits for f in fields)} bits")

    def report_map(self):
        out = _item(USAGE_PAGE, self.page) + _item(USAGE, self.usage)
        out += _item(COLLECTION, 0x01)  # Application
        if self.report_id is not None:
            out += _item(REPORT_ID, self.report_id)
        for f in self.fields:
            out += f.descriptor()
        return out + _item(END_COLLECTION, size=0)

    @property
    def report_len(self):
        """Payload bytes, excluding the Report ID prefix."""
        return sum(f.bits for f in self.fields) // 8

    def report(self, active=()):
        """Bytes for a report in which `active` (page, usage) controls are pressed.

        A Report ID, when declared, is transmitted as the first byte of every
        report (hidintro.html), so it is prepended here.
        """
        bits = bytearray(self.report_len)
        offset = 0
        for f in self.fields:
            if isinstance(f, Padding):
                offset += f.bits
                continue
            if f.variable:
                # Each usage owns one bit, in usage order from usage_min.
                for page, usage in active:
                    if page != f.page or not (f.usage_min <= usage <= f.usage_max):
                        continue
                    bit = offset + (usage - f.usage_min) * f.size
                    bits[bit // 8] |= 1 << (bit % 8)
            else:
                # Array: successive slots hold the usage codes that are active.
                slot = 0
                for page, usage in active:
                    if page != f.page or slot >= f.count:
                        continue
                    bits[(offset // 8) + slot] = usage
                    slot += 1
            offset += f.bits
        payload = bytes(bits)
        return (bytes([self.report_id]) + payload
                if self.report_id is not None else payload)


# --- Device shapes ----------------------------------------------------------
# Each is a declaration, not a capture. Nothing below hardcodes a report byte.

def boot_keyboard(report_id=1):
    """Boot-protocol keyboard: modifier bitfield, reserved byte, 6 key slots.

    The key slots are an ARRAY -- they hold usage codes, not bit positions -- which
    is why an arrow key arrives as its usage (Right Arrow = 0x4F) rather than a bit.
    """
    return Device("boot-keyboard", PAGE_GENERIC_DESKTOP, USAGE_KEYBOARD, [
        Field(PAGE_KEYBOARD, 0xE0, 0xE7, count=8, size=1, variable=True),
        Padding(8),
        Field(PAGE_KEYBOARD, 0x00, 0x65, count=6, size=8, variable=False),
    ], report_id=report_id)


def button_bitfield(buttons=8, report_id=None):
    """A button bitmap: `buttons` controls, one bit each, padded to a byte.

    This is the shape a Free 2 presents. Button N sets bit N-1, so button 6 gives
    0x20 and button 3 gives 0x04 -- derived, not copied.
    """
    pad = (-buttons) % 8
    fields = [Field(PAGE_BUTTON, 1, buttons, count=buttons, size=1, variable=True)]
    if pad:
        fields.append(Padding(pad))
    return Device("button-bitfield", PAGE_GENERIC_DESKTOP, 0x00, fields,
                  report_id=report_id)


def consumer_array(report_id=2, slots=1):
    """Consumer Control reporting usage codes in array slots (media remotes)."""
    return Device("consumer-array", PAGE_CONSUMER, USAGE_CONSUMER_CONTROL, [
        Field(PAGE_CONSUMER, 0x00, 0xFF, count=slots, size=8, variable=False,
              logical_max=0xFF),
    ], report_id=report_id)
