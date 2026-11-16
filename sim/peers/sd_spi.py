# Adapted from a SPI-mode microSD peer (SD Physical Layer Simplified Spec v9, Part 1 7): SDHC, block
# addressing. Reads sectors from the FAT image named by $SD_IMAGE (made at run time by sim/run.sh, never
# committed); writes stay in memory. Everything else is as the original.
# microSD card in SPI mode (SD Physical Layer Simplified Specification v9,
# Part 1 §7) as a ScriptedSpiSlave: SDHC (CCS=1, block addressing), 64 MiB,
# freshly formatted as an empty FAT32 volume so FatFS mounts it and the
# firmware logs. Unwritten sectors read as zeros. Replace `blocks` (dict of
# 512-byte sectors) to present a card image.
#
import os

# Command set ChibiOS mmc_spi / FatFS exercise: CMD0, CMD8, CMD55+ACMD41,
# CMD58, CMD1, CMD16, CMD9, CMD10, then CMD18 streams (stopped by CMD12) and
# CMD25 streams (0xFC per block, 0xFD stop); CMD17/CMD24/CMD13/CMD59 too. Timing
# constants below are declared spec values, tunable.

NCR_BEATS = 1                   # command-response gap, 1..8 bytes (spec §7.5.1)
READ_ACCESS_BEATS = 2           # data token latency for a read (N_AC, tunable)
WRITE_BUSY_US = 1_000           # card busy after a block write (typ ms-class, tunable)
INIT_POLLS = 2                  # ACMD41 answers "busy" this many times (card power-up)
CAPACITY_BLOCKS = 64 * 1024 * 1024 // 512   # CSD capacity; the image may be smaller (rest reads zeros)
OCR = bytes([0xC0, 0xFF, 0x80, 0x00])   # powered up, CCS=1 (SDHC), 2.7-3.6 V

R1_IDLE, R1_ILLEGAL = 0x01, 0x04
TOKEN_START, TOKEN_ACCEPTED = 0xFE, 0x05                # single-block data token, data response
TOKEN_MULTI, TOKEN_STOP_TRAN = 0xFC, 0xFD               # CMD25 block token, stop tran (spec §7.3.3.2)


def _csd_v2(blocks):
    c_size = blocks // 1024 - 1                         # capacity = (C_SIZE+1) * 512 KiB
    csd = bytearray(16)
    csd[0] = 0x40                                       # CSD_STRUCTURE 1 (v2.0)
    csd[1] = 0x0E                                       # TAAC
    csd[3] = 0x32                                       # TRAN_SPEED 25 MHz
    csd[4] = 0x5B                                       # CCC high
    csd[5] = 0x59                                       # CCC low | READ_BL_LEN 9 (512)
    csd[7] = (c_size >> 16) & 0x3F
    csd[8] = (c_size >> 8) & 0xFF
    csd[9] = c_size & 0xFF
    csd[10] = 0x7F
    csd[11] = 0x80
    csd[12] = 0x0A
    csd[13] = 0x40
    csd[15] = 0x01
    return bytes(csd)


CID = bytes([0x03, 0x53, 0x44, 0x53, 0x49, 0x4D, 0x41, 0x4E, 0x10, 0x00, 0x59, 0x2E, 0x54, 0x01, 0x7A, 0x01])


class Slave:
    def __init__(self, ctx):
        self.ctx = ctx
        self.blocks = {}                                # writes, over the image
        self.idle = True                                # until ACMD41 completes
        self.app_cmd = False
        self.polls = 0
        self.img = open(os.environ["SD_IMAGE"], "rb") if os.environ.get("SD_IMAGE") else None
        self.cmd = bytearray()
        self.out = bytearray()                          # queued response bytes
        self.read_block = None                          # next block of a CMD18 stream
        self.write_block = None                         # block awaiting data (CMD24/CMD25)
        self.write_token = TOKEN_START
        self.write_buf = bytearray()
        self.program_pending = False                    # go busy once the data response is out

    def _resp(self, r1, payload=b"", data_block=None):
        self.out += bytes([0xFF] * NCR_BEATS) + bytes([r1]) + payload
        if data_block is not None:
            self._queue_block(data_block)

    def _queue_block(self, data):
        self.out += bytes([0xFF] * READ_ACCESS_BEATS) + bytes([TOKEN_START]) + data + b"\xFF\xFF"

    def _block(self, n):
        if n in self.blocks:
            return self.blocks[n]
        if self.img:
            self.img.seek(n * 512)
            return self.img.read(512).ljust(512, b"\0")
        return bytes(512)

    def _r1(self):
        return R1_IDLE if self.idle else 0x00

    def _command(self, c):
        cmd, arg = c[0] & 0x3F, int.from_bytes(c[1:5], "big")
        app, self.app_cmd = self.app_cmd, False
        if cmd == 0:
            self.idle = True
            self._resp(R1_IDLE)
        elif cmd == 8:
            self._resp(self._r1(), bytes([0x00, 0x00, 0x01, 0xAA]))     # R7: 2.7-3.6 V, echo
        elif cmd == 55:
            self.app_cmd = True
            self._resp(self._r1())
        elif cmd == 41 and app:
            self.polls += 1
            if self.polls >= INIT_POLLS:
                self.idle = False
            self._resp(self._r1())
        elif cmd == 58:
            self._resp(self._r1(), OCR)
        elif cmd == 9:
            self._resp(self._r1(), data_block=_csd_v2(CAPACITY_BLOCKS))
        elif cmd == 10:
            self._resp(self._r1(), data_block=CID)
        elif cmd == 1:
            # SEND_OP_COND is valid for SD cards in SPI mode (spec §7.3.1.3); ChibiOS
            # mmc_spi sends it after the SDHC detection and needs R1 0x00 to proceed.
            self._resp(self._r1())
        elif cmd in (16, 59):
            self._resp(self._r1())
        elif cmd == 12:                                                   # STOP_TRANSMISSION ends a CMD18 stream
            self.read_block = None
            self.out = bytearray()
            self._resp(self._r1())
        elif cmd == 13:
            self._resp(self._r1(), bytes([0x00]))                         # R2
        elif cmd in (17, 18):
            if arg >= CAPACITY_BLOCKS:
                self._resp(0x40)                                          # out of range
            else:
                self._resp(0x00, data_block=self._block(arg))
                if cmd == 18:
                    self.read_block = arg + 1                             # keeps streaming until CMD12
        elif cmd in (24, 25):
            if arg >= CAPACITY_BLOCKS:
                self._resp(0x40)
            else:
                self._resp(0x00)
                self.write_block = arg
                self.write_token = TOKEN_START if cmd == 24 else TOKEN_MULTI
                self.write_buf = bytearray()
        else:
            self._resp(self._r1() | R1_ILLEGAL)

    def _write_byte(self, b):
        if self.ctx.busy:                               # programming: DO held low
            return 0x00
        if not self.write_buf:
            if b == TOKEN_STOP_TRAN:                    # ends CMD25; card programs, then idles
                self.write_block = None
                self.ctx.busy_until(self.ctx.now_us + WRITE_BUSY_US)
                return 0xFF
            if b != self.write_token:
                return 0xFF                             # waiting for the data token
        self.write_buf.append(b)
        if len(self.write_buf) == 1 + 512 + 2:          # token + data + CRC (ignored)
            self.blocks[self.write_block] = bytes(self.write_buf[1:513])
            self.write_buf = bytearray()
            self.write_block = None if self.write_token == TOKEN_START else self.write_block + 1
            self.out = bytearray([TOKEN_ACCEPTED])      # data response on the next byte, then busy
            self.program_pending = True
        return 0xFF

    def on_select(self):
        self.cmd = bytearray()

    def on_deselect(self):
        self.cmd = bytearray()
        self.read_block = None                          # CS high abandons a read stream
        self.out = bytearray()

    def on_finish(self):
        pass

    def on_transmit(self, b):
        if self.write_block is not None and not self.out:
            return self._write_byte(b)
        if self.cmd or (b & 0xC0) == 0x40:              # command frame; CMD12 arrives mid-stream
            self.cmd.append(b)
            if len(self.cmd) == 6:
                c, self.cmd = bytes(self.cmd), bytearray()
                self._command(c)
                return 0xFF
        if self.out:
            r = self.out.pop(0)
            if not self.out:
                if self.program_pending:
                    self.program_pending = False
                    self.ctx.busy_until(self.ctx.now_us + WRITE_BUSY_US)
                elif self.read_block is not None:       # next block of the CMD18 stream
                    self._queue_block(self._block(self.read_block))
                    self.read_block += 1
            return r
        return 0x00 if self.ctx.busy else 0xFF
