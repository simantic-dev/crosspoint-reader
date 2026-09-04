"""Descriptor-driven HID report construction, and what the host's hints get wrong.

These run anywhere: no simulator, no hardware, no payload. They exist to keep the
bench honest about the difference between declaring a device and copying one.
"""
import pytest

from hid_descriptor import (PAGE_BUTTON, PAGE_CONSUMER, PAGE_GENERIC_DESKTOP,
                            PAGE_KEYBOARD, Device, Field, boot_keyboard,
                            button_bitfield, consumer_array)


class TestReportGeometry:
    def test_reports_are_whole_bytes(self):
        """HID reports are byte-aligned; the helpers pad to reach a boundary."""
        assert len(button_bitfield(5).report()) == 1   # 5 buttons + 3 pad bits
        assert len(button_bitfield(9).report()) == 2   # 9 buttons + 7 pad bits

    def test_an_unpadded_descriptor_is_rejected(self):
        """A field run that does not land on a byte boundary is a malformed
        descriptor, and saying so at construction beats emitting a short report."""
        with pytest.raises(ValueError, match="whole bytes"):
            Device("bad", PAGE_GENERIC_DESKTOP, 0x00,
                   [Field(PAGE_BUTTON, 1, 5, count=5, size=1, variable=True)])

    def test_report_id_is_the_first_byte(self):
        """A declared Report ID is transmitted as the first byte of every report."""
        assert button_bitfield(8, report_id=None).report() == b"\x00"
        assert button_bitfield(8, report_id=7).report() == b"\x07\x00"


class TestVariableVersusArray:
    """The Input item's Variable-vs-Array flag is what decides a control's encoding.

    Getting this backwards is the single most common way to mis-model a remote, and
    it is invisible if you only ever copy one device's bytes.
    """

    def test_variable_fields_give_each_control_its_own_bit(self):
        bf = button_bitfield(8)
        assert bf.report([(PAGE_BUTTON, 1)]) == b"\x01"
        assert bf.report([(PAGE_BUTTON, 8)]) == b"\x80"
        # Independent bits: two controls held at once set two bits.
        assert bf.report([(PAGE_BUTTON, 1), (PAGE_BUTTON, 8)]) == b"\x81"

    def test_array_fields_carry_usage_codes_in_slots(self):
        kb = boot_keyboard()
        # 0x4F is Right Arrow's usage code. In an array it appears verbatim in the
        # first free slot -- it is NOT bit 79 of a bitfield.
        assert kb.report([(PAGE_KEYBOARD, 0x4F)])[3] == 0x4F
        # A second key fills the next slot rather than OR-ing into the first.
        two = kb.report([(PAGE_KEYBOARD, 0x4F), (PAGE_KEYBOARD, 0x50)])
        assert (two[3], two[4]) == (0x4F, 0x50)

    def test_the_same_control_number_encodes_differently_per_field_type(self):
        """Control 6 is 0x20 in a bitfield and 0x06 in an array. Same "button 6"."""
        assert button_bitfield(8).report([(PAGE_BUTTON, 6)]) == b"\x20"
        assert consumer_array().report([(PAGE_CONSUMER, 6)])[1] == 0x06


class TestKnownDevicesAreReproducedNotCopied:
    """Both shapes below are declared, then checked against what real hardware
    emits. No report byte is hardcoded anywhere in hid_descriptor.py."""

    def test_boot_keyboard_matches_a_real_arrow_key_report(self):
        assert boot_keyboard().report([(PAGE_KEYBOARD, 0x4F)]).hex() == (
            "0100004f0000000000")

    def test_free2_page_turner_falls_out_of_a_24_button_bitfield(self):
        """A Hanlinyue Free 2 emits 20 00 00 / 04 00 00 / 00 00 00 (teardown on
        crosspoint-reader#2418). Those are buttons 6 and 3 of a 3-byte button
        bitmap -- nothing about the device is special."""
        bf = button_bitfield(24)
        assert bf.report([(PAGE_BUTTON, 6)]).hex() == "200000"
        assert bf.report([(PAGE_BUTTON, 3)]).hex() == "040000"
        assert bf.report().hex() == "000000"


# --- What the host currently does with a report map --------------------------

def naive_usage_page_scan(report_map):
    """Faithful transcription of BleKeyboardHost::parseReportMapHints().

    Kept here so its behaviour is executable and testable rather than argued
    about. It walks bytes, not HID items:

        for (size_t i = 0; i + 1 < len; ++i)
          if (map[i] == 0x05) { if (map[i+1] == 0x07) kbd = true;
                                else if (map[i+1] == 0x0C) consumer = true; }
    """
    kbd = consumer = False
    for i in range(len(report_map) - 1):
        if report_map[i] == 0x05:
            if report_map[i + 1] == 0x07:
                kbd = True
            elif report_map[i + 1] == 0x0C:
                consumer = True
    return kbd, consumer


def item_walk_usage_pages(report_map):
    """A correct short-item walk (USB HID 1.11 sec 6.2.2.2).

    bSize lives in the low two bits, with 3 meaning four data bytes, so data is
    skipped rather than re-read as items.
    """
    pages = set()
    i = 0
    while i < len(report_map):
        prefix = report_map[i]
        size = prefix & 0x03
        size = 4 if size == 3 else size
        tag_type = prefix & 0xFC
        if tag_type == 0x04:  # Usage Page
            pages.add(int.from_bytes(report_map[i + 1:i + 1 + size], "little"))
        i += 1 + size
    return pages


class TestReportMapHintsAreNotAParser:
    def test_it_agrees_with_a_real_walk_on_ordinary_descriptors(self):
        """A positive control: on a plain keyboard the shortcut is right, which is
        why the defect below survived."""
        kbd, _ = naive_usage_page_scan(boot_keyboard().report_map())
        assert kbd is True
        assert PAGE_KEYBOARD in item_walk_usage_pages(boot_keyboard().report_map())

    def test_it_false_positives_on_data_bytes_that_look_like_an_item(self):
        """0x0A 0x05 0x07 is a legal 2-byte Usage item (Usage 0x0705). Its DATA
        bytes are 05 07, which the byte scan reads as Usage Page (Keyboard)."""
        descriptor = bytes([0x0A, 0x05, 0x07])
        kbd, _ = naive_usage_page_scan(descriptor)
        assert kbd is True, "scan claims a keyboard page"
        assert PAGE_KEYBOARD not in item_walk_usage_pages(descriptor), (
            "but the descriptor declares no keyboard page at all")

    def test_it_cannot_see_a_two_byte_usage_page(self):
        """Vendor-defined pages are declared with 0x06 nn nn, which the scan (which
        only matches 0x05) never sees -- so vendor remotes get no hint at all."""
        descriptor = bytes([0x06, 0x00, 0xFF])  # Usage Page (Vendor 0xFF00)
        kbd, consumer = naive_usage_page_scan(descriptor)
        assert (kbd, consumer) == (False, False)
        assert item_walk_usage_pages(descriptor) == {0xFF00}
