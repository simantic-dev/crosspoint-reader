# Scripted BLE HID-over-GATT page-turner (ScriptedBlePeer Python model).
# Advertising (name "PageTurner", service 0x1812) is the C# model's job; this
# file serves the GATT a HID host walks after connecting: the HID service with
# a keyboard report map and one input report, plus Battery and Device Info,
# which HID hosts commonly read during enumeration.
#
# The link layer, ARQ and LLCP are the C# model's. SMP is not modelled by the
# peer at all -- if the CrossPoint host insists on bonding before subscribing,
# that will be visible as an SMP Pairing Request PDU this script never answers.

ATT_ERROR_RSP = 0x01
ATT_FIND_INFORMATION_REQ = 0x04
ATT_FIND_INFORMATION_RSP = 0x05
ATT_FIND_BY_TYPE_VALUE_REQ = 0x06
ATT_FIND_BY_TYPE_VALUE_RSP = 0x07
ATT_READ_BY_TYPE_REQ = 0x08
ATT_READ_BY_TYPE_RSP = 0x09
ATT_READ_REQ = 0x0A
ATT_READ_RSP = 0x0B
ATT_READ_BLOB_REQ = 0x0C
ATT_READ_BLOB_RSP = 0x0D
ATT_READ_BY_GROUP_TYPE_REQ = 0x10
ATT_READ_BY_GROUP_TYPE_RSP = 0x11
ATT_WRITE_REQ = 0x12
ATT_WRITE_RSP = 0x13
ATT_WRITE_CMD = 0x52

ATT_ERR_ATTRIBUTE_NOT_FOUND = 0x0A
ATT_ERR_INVALID_OFFSET = 0x07

# The peer answers Exchange MTU with the 23-octet default (Core Vol 3 Part G
# 5.2.1), so a response body is at most ATT_MTU - 1 octets. The HID Report Map
# is longer than that, which is exactly the case Read Blob exists for.
ATT_MTU = 23

UUID_PRIMARY_SERVICE = 0x2800
UUID_CHARACTERISTIC = 0x2803
UUID_CCC = 0x2902
UUID_REPORT_REFERENCE = 0x2908

UUID_HID_SERVICE = 0x1812
UUID_HID_INFORMATION = 0x2A4A
UUID_REPORT_MAP = 0x2A4B
UUID_HID_CONTROL_POINT = 0x2A4C
UUID_REPORT = 0x2A4D
UUID_PROTOCOL_MODE = 0x2A4E
UUID_BATTERY_SERVICE = 0x180F
UUID_BATTERY_LEVEL = 0x2A19

CHAR_PROP_READ = 0x02
CHAR_PROP_WRITE_NR = 0x04
CHAR_PROP_WRITE = 0x08
CHAR_PROP_NOTIFY = 0x10

# -- handle layout ----------------------------------------------------------
# Battery service first (hosts read it during enumeration), HID second.
H_BAT_SERVICE = 0x0001        # group 0x0001..0x0003
H_BAT_DECL = 0x0002
H_BAT_VALUE = 0x0003
H_HID_SERVICE = 0x0010        # group 0x0010..0x001B
H_HIDINFO_DECL = 0x0011
H_HIDINFO_VALUE = 0x0012
H_REPORTMAP_DECL = 0x0013
H_REPORTMAP_VALUE = 0x0014
H_PROTOMODE_DECL = 0x0015
H_PROTOMODE_VALUE = 0x0016
H_REPORT_DECL = 0x0017
H_REPORT_VALUE = 0x0018
H_REPORT_CCC = 0x0019
H_REPORT_REF = 0x001A         # Report Reference: report ID 1, input
H_CTRLPOINT_DECL = 0x001B
H_CTRLPOINT_VALUE = 0x001C
H_HID_LAST = H_CTRLPOINT_VALUE

# Minimal keyboard report map: 8-byte boot-style input report (modifiers,
# reserved, 6 keycodes), report ID 1.
REPORT_MAP = bytes([
    0x05, 0x01,        # Usage Page (Generic Desktop)
    0x09, 0x06,        # Usage (Keyboard)
    0xA1, 0x01,        # Collection (Application)
    0x85, 0x01,        #   Report ID (1)
    0x05, 0x07,        #   Usage Page (Key Codes)
    0x19, 0xE0, 0x29, 0xE7,  # Usage Min/Max (modifiers)
    0x15, 0x00, 0x25, 0x01,  # Logical 0..1
    0x75, 0x01, 0x95, 0x08,  # 1 bit x 8
    0x81, 0x02,        #   Input (Data, Var, Abs) - modifier byte
    0x75, 0x08, 0x95, 0x01,  # 8 bits x 1
    0x81, 0x01,        #   Input (Const) - reserved
    0x75, 0x08, 0x95, 0x06,  # 8 bits x 6
    0x15, 0x00, 0x25, 0x65,  # Logical 0..0x65
    0x19, 0x00, 0x29, 0x65,  # Usage 0..0x65
    0x81, 0x00,        #   Input (Data, Array) - keys
    0xC0,              # End Collection
])

# HID Information: bcdHID 1.11, country 0, flags RemoteWake|NormallyConnectable
HID_INFO = bytes([0x11, 0x01, 0x00, 0x03])

KEY_RIGHT_ARROW = 0x4F

SERVICES = [
    # (service handle, last handle, 16-bit uuid)
    (H_BAT_SERVICE, H_BAT_VALUE, UUID_BATTERY_SERVICE),
    (H_HID_SERVICE, H_HID_LAST, UUID_HID_SERVICE),
]

CHARACTERISTICS = [
    # (declaration handle, value handle, properties, uuid)
    (H_BAT_DECL, H_BAT_VALUE, CHAR_PROP_READ, UUID_BATTERY_LEVEL),
    (H_HIDINFO_DECL, H_HIDINFO_VALUE, CHAR_PROP_READ, UUID_HID_INFORMATION),
    (H_REPORTMAP_DECL, H_REPORTMAP_VALUE, CHAR_PROP_READ, UUID_REPORT_MAP),
    (H_PROTOMODE_DECL, H_PROTOMODE_VALUE,
     CHAR_PROP_READ | CHAR_PROP_WRITE_NR, UUID_PROTOCOL_MODE),
    (H_REPORT_DECL, H_REPORT_VALUE,
     CHAR_PROP_READ | CHAR_PROP_NOTIFY, UUID_REPORT),
    (H_CTRLPOINT_DECL, H_CTRLPOINT_VALUE, CHAR_PROP_WRITE_NR, UUID_HID_CONTROL_POINT),
]

DESCRIPTORS = {
    H_REPORT_CCC: UUID_CCC,
    H_REPORT_REF: UUID_REPORT_REFERENCE,
}

READ_VALUES = {
    H_BAT_VALUE: bytes([90]),
    H_HIDINFO_VALUE: HID_INFO,
    H_REPORTMAP_VALUE: REPORT_MAP,
    H_PROTOMODE_VALUE: bytes([0x01]),          # report protocol
    H_REPORT_VALUE: bytes(8),
    H_REPORT_REF: bytes([0x01, 0x01]),         # report ID 1, input report
}

# Once subscribed, send one "right arrow" press/release pair -- the page turn.
KEYPRESS_DELAY_US = 100_000


def u16(value):
    return bytes([value & 0xFF, (value >> 8) & 0xFF])


class Peer:
    def __init__(self, ctx):
        self.ctx = ctx
        self.subscribed = False
        self.queue = []

    def on_connect(self):
        self.subscribed = False
        self.queue = []
        self.ctx.info("connected; serving HID over GATT")

    def on_disconnect(self, reason):
        self.ctx.info(f"disconnected (reason 0x{reason:02x})")

    def on_timer(self):
        if not self.queue:
            return
        report = self.queue.pop(0)
        self.ctx.info("sending HID report %s" % report.hex())
        self.ctx.notify(H_REPORT_VALUE, report)
        if self.queue:
            self.ctx.schedule_oneshot(KEYPRESS_DELAY_US)

    def on_att(self, pdu):
        opcode = pdu[0]
        if opcode == ATT_READ_BY_GROUP_TYPE_REQ:
            self._read_by_group_type(pdu)
        elif opcode == ATT_READ_BY_TYPE_REQ:
            self._read_by_type(pdu)
        elif opcode == ATT_FIND_BY_TYPE_VALUE_REQ:
            self._find_by_type_value(pdu)
        elif opcode == ATT_FIND_INFORMATION_REQ:
            self._find_information(pdu)
        elif opcode == ATT_READ_REQ:
            self._read(pdu)
        elif opcode == ATT_READ_BLOB_REQ:
            self._read_blob(pdu)
        elif opcode == ATT_WRITE_REQ:
            self._write(pdu, respond=True)
        elif opcode == ATT_WRITE_CMD:
            self._write(pdu, respond=False)
        elif opcode % 2 == 0 and not opcode & 0x40:
            # A request left unanswered stalls the client's ATT queue forever.
            self._error(pdu, ATT_ERR_ATTRIBUTE_NOT_FOUND)

    # -- discovery ---------------------------------------------------------

    def _read_by_group_type(self, pdu):
        start, end, uuid = self._range_and_uuid(pdu)
        if uuid != UUID_PRIMARY_SERVICE:
            self._error(pdu, ATT_ERR_ATTRIBUTE_NOT_FOUND)
            return
        for svc, last, svc_uuid in SERVICES:
            if start <= svc <= end:
                self.ctx.send(bytes([ATT_READ_BY_GROUP_TYPE_RSP, 6])
                              + u16(svc) + u16(last) + u16(svc_uuid))
                return
        self._error(pdu, ATT_ERR_ATTRIBUTE_NOT_FOUND)

    def _find_by_type_value(self, pdu):
        start = pdu[1] | (pdu[2] << 8)
        end = pdu[3] | (pdu[4] << 8)
        attr_type = pdu[5] | (pdu[6] << 8)
        value = pdu[7] | (pdu[8] << 8) if len(pdu) >= 9 else None
        if attr_type == UUID_PRIMARY_SERVICE:
            for svc, last, svc_uuid in SERVICES:
                if value == svc_uuid and start <= svc <= end:
                    self.ctx.send(bytes([ATT_FIND_BY_TYPE_VALUE_RSP])
                                  + u16(svc) + u16(last))
                    return
        self._error(pdu, ATT_ERR_ATTRIBUTE_NOT_FOUND)

    def _read_by_type(self, pdu):
        start, end, uuid = self._range_and_uuid(pdu)
        if uuid == UUID_CHARACTERISTIC:
            for decl, value_handle, props, char_uuid in CHARACTERISTICS:
                if start <= decl <= end:
                    self.ctx.send(bytes([ATT_READ_BY_TYPE_RSP, 7])
                                  + u16(decl) + bytes([props])
                                  + u16(value_handle) + u16(char_uuid))
                    return
        elif uuid == UUID_REPORT_REFERENCE and start <= H_REPORT_REF <= end:
            # Read-by-type on the Report Reference is how a HID host maps
            # report handles to report IDs.
            self.ctx.send(bytes([ATT_READ_BY_TYPE_RSP, 4])
                          + u16(H_REPORT_REF) + READ_VALUES[H_REPORT_REF])
            return
        self._error(pdu, ATT_ERR_ATTRIBUTE_NOT_FOUND)

    def _find_information(self, pdu):
        start = pdu[1] | (pdu[2] << 8)
        end = pdu[3] | (pdu[4] << 8)
        for handle in sorted(DESCRIPTORS):
            if start <= handle <= end:
                self.ctx.send(bytes([ATT_FIND_INFORMATION_RSP, 0x01])
                              + u16(handle) + u16(DESCRIPTORS[handle]))
                return
        self._error(pdu, ATT_ERR_ATTRIBUTE_NOT_FOUND)

    # -- reads / writes ----------------------------------------------------

    def _read(self, pdu):
        handle = pdu[1] | (pdu[2] << 8)
        if handle in READ_VALUES:
            # Core Vol 3 Part F 3.4.4.4: the response carries at most ATT_MTU-1
            # octets of the value. A client that sees a full-length response
            # asks for the rest with Read Blob, so truncating here is the
            # protocol, not a shortcut.
            self.ctx.send(bytes([ATT_READ_RSP]) + READ_VALUES[handle][:ATT_MTU - 1])
        elif handle == H_REPORT_CCC:
            self.ctx.send(bytes([ATT_READ_RSP, 1 if self.subscribed else 0, 0x00]))
        else:
            self._error(pdu, ATT_ERR_ATTRIBUTE_NOT_FOUND)

    def _read_blob(self, pdu):
        # Core Vol 3 Part F 3.4.4.5: continue a value the Read Response had to
        # truncate. An offset equal to the value's length is legal and answers
        # with an empty body -- that is how the client learns it has the whole
        # value; only an offset PAST the end is an error.
        handle = pdu[1] | (pdu[2] << 8)
        offset = pdu[3] | (pdu[4] << 8)
        if handle not in READ_VALUES:
            self._error(pdu, ATT_ERR_ATTRIBUTE_NOT_FOUND)
            return
        value = READ_VALUES[handle]
        if offset > len(value):
            self._error(pdu, ATT_ERR_INVALID_OFFSET)
            return
        self.ctx.send(bytes([ATT_READ_BLOB_RSP])
                      + value[offset:offset + ATT_MTU - 1])

    def _write(self, pdu, respond):
        handle = pdu[1] | (pdu[2] << 8)
        if handle == H_REPORT_CCC:
            self.subscribed = len(pdu) >= 4 and (pdu[3] & 0x01) != 0
            if respond:
                self.ctx.send(bytes([ATT_WRITE_RSP]))
            if self.subscribed:
                # Page turn: right-arrow press, then release.
                self.queue = [bytes([0x01, 0x00, 0x00, KEY_RIGHT_ARROW, 0, 0, 0, 0, 0]),
                              bytes([0x01, 0x00, 0x00, 0, 0, 0, 0, 0, 0])]
                self.ctx.schedule_oneshot(KEYPRESS_DELAY_US)
        elif handle in (H_PROTOMODE_VALUE, H_CTRLPOINT_VALUE):
            if respond:
                self.ctx.send(bytes([ATT_WRITE_RSP]))
        else:
            if respond:
                self._error(pdu, ATT_ERR_ATTRIBUTE_NOT_FOUND)

    def _range_and_uuid(self, pdu):
        start = pdu[1] | (pdu[2] << 8)
        end = pdu[3] | (pdu[4] << 8)
        uuid = pdu[5] | (pdu[6] << 8)
        return start, end, uuid

    def _error(self, pdu, code):
        handle = (pdu[1] | (pdu[2] << 8)) if len(pdu) >= 3 else 0
        self.ctx.send(bytes([ATT_ERROR_RSP, pdu[0]]) + u16(handle) + bytes([code]))
