# Non-halting stage tracer for the BLE scan -> HCI advertising-report chain.
#
# The C3 radio model fills RX descriptors during a CrossPoint scan (1451 fills in
# the head build's run) and NimBLE still reports devices=0, so the packet is lost
# somewhere between the LL's descriptor check and the host's GAP callback. Each
# address below is one stage of that chain; the last label that appears in the
# trace is the stage that still runs, and the first one missing is where it dies.
#
# ctx.watch installs a C# execution hook (no gdb halt, no timing perturbation).
# Addresses are from the current firmware build's symbols
# (crosspoint-feat-bt-sim/.pio/build/default/firmware.elf, riscv32-esp-elf-nm);
# the 0x4038xxxx ones live in the BT controller blob's IRAM patch region and the
# 0x42xxxxxx ones in this build's flash, so they must be re-read after a rebuild.
#
# This model stands in for `btmac` and keeps bletrace.py's store+read+log
# behaviour: the watches have to be armed from a real MMIO access, because the
# scripted models never receive the INIT access in this run (the machine is not
# reset after the platform is loaded), and ctx.watch is not yet wired during
# __init__.

STAGES = [
    (0x40382F64, "A-rxdesc_check_hack"),
    (0x403853F4, "B-scan_process_pkt_rx_hack"),
    (0x40385682, "C-scan_pkt_rx_adv_rep_hack"),
    (0x40386332, "D-llm_adv_rep_flow_control_check"),
    (0x42190A3A, "E-lld_adv_rep_ind_handler_hack"),
    (0x42190304, "F-f_lld_adv_rep_ind_handler_hack"),
    (0x42063F8A, "G-ble_hs_hci_evt_le_meta"),
    (0x42064120, "H-ble_hs_hci_evt_le_adv_rpt"),
    (0x4205DAD0, "I-ble_gap_rx_adv_report"),
    (0x42057A1A, "J-NimBLEScan_handleGapEvent"),
    (0x4206EA42, "K-ScanCB_onResult"),
    (0x4206DCDE, "L-onScanResultIngest"),
]


class Peripheral:
    def __init__(self, ctx):
        self.ctx = ctx
        self.storage = {}
        self.armed = False

    def arm(self):
        self.armed = True
        try:
            self.ctx.watch_to("scanwatch.log")
            for addr, label in STAGES:
                self.ctx.watch(addr, label)
            open("scanwatch-arm.log", "w").write("armed %d\n" % len(STAGES))
        except Exception as exc:  # ctx logs are filtered out of a normal run
            open("scanwatch-arm.log", "w").write("FAILED: %r\n" % (exc,))
        self.ctx.schedule_periodic(200000)  # 200 ms: poll the EM control structures

    # --- Exchange-Memory dump ------------------------------------------------
    # Replicates what ESP32C3_Radio.TrySendScanRequest does when it decides
    # whether this node is an ACTIVE scanner: resolve the CS segment from the EM
    # mapping registers and walk its TX-descriptor ring looking for a SCAN_REQ
    # (TXPHADV low nibble == 3). The model only ever looks at segment index 1
    # (the ADV control structure), so if the scan activity's descriptors live in
    # another segment the model silently behaves like a passive scanner.

    EM_MAP = 0x60031204          # mapping registers, one per EM segment
    EM_PHYS = 0x3FC00000
    EM_MASK = 0xFFFFC
    EM_LUT = 0x3FF1F518          # ROM em_base_reg_lut

    def seg_base(self, index):
        reg = self.ctx.bus_read(self.EM_MAP + index * 4, 4)
        return self.EM_PHYS | ((reg << 2) & self.EM_MASK)

    def em(self, offset):
        lut = self.EM_LUT + (offset >> 10) * 4
        map_idx = self.ctx.bus_read(lut, 1)
        region_base = self.ctx.bus_read(lut + 2, 2)
        reg = self.ctx.bus_read(self.EM_MAP + map_idx * 4, 4)
        return (self.EM_PHYS | ((reg << 2) & self.EM_MASK)) + (offset - region_base)

    def ring(self, cs_base):
        """TXPHADV headers of the TX-descriptor ring hanging off CS+0x1c."""
        out = []
        try:
            offset = self.ctx.bus_read(cs_base + 0x1C, 2)
            for _ in range(4):
                if offset == 0:
                    break
                desc = self.em(offset)
                out.append("%04x:txphadv=%04x" % (offset, self.ctx.bus_read(desc + 2, 2)))
                offset = self.ctx.bus_read(desc, 2) & 0x7FFF
        except Exception as exc:
            out.append("err %r" % (exc,))
        return out

    def rx_header(self):
        """Header byte of the RX descriptor the model last filled (bit6 = TxAdd
        of the advertiser's address: 1 = random, which the SCAN_REQ we send back
        has to mirror into its own RxAdd bit or the advertiser ignores it)."""
        try:
            p_lld_env = self.ctx.bus_read(0x3FCDFF9C, 4)
            if p_lld_env == 0:
                return "rx=?"
            idx = self.ctx.bus_read(p_lld_env + 216, 1)
            desc = self.em(0x1000) + idx * 20
            return "rxdesc%d hdr=%02x len=%d" % (idx, self.ctx.bus_read(desc + 4, 1),
                                                 self.ctx.bus_read(desc + 5, 1))
        except Exception as exc:
            return "rx err %r" % (exc,)

    def on_timer(self):
        try:
            segs = []
            for i in range(8):
                base = self.seg_base(i)
                if base == self.EM_PHYS:
                    continue  # unmapped segment
                segs.append("seg%d@%08x[%s]" % (i, base, ",".join(self.ring(base))))
            line = " ".join(segs) + " " + self.rx_header()
        except Exception as exc:
            line = "err %r" % (exc,)
        if line != getattr(self, "last_dump", None):
            self.last_dump = line
            with open("scanwatch-em.log", "a") as f:
                f.write(line + "\n")

    def on_access(self, req):
        if not self.armed:
            self.arm()
        if req.is_init:
            self.storage.clear()
            return
        if req.is_write:
            self.storage[req.offset] = req.value
        elif req.is_read:
            req.value = self.storage.get(req.offset, 0)
