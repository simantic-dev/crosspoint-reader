"""The reopen/resume crash (repro_reopen_crash.py) re-run with BLE ON and a HID
page-turner paired, which is what feat-ble is for.

repro_reopen_crash.py answers what a resumed section build does with the radio
off. This answers the same question with the radio on, so the two runs differ in
one thing only: whether NimBLE's ~53 KB and a live connection are resident while
the build walks the chapter.

The walk, all of it verified from the RTC ring rather than assumed:

  boot                   feat-ble resumes into /bible.epub by itself
  reader menu -> Go Home the partial cache is written here (same rows as the
                         BLE-off repro, found the same self-correcting way)
  Home -> up -> confirm  Settings is the LAST Home row, so one `up` wraps to it
                         instead of counting downs past conditional rows
  confirm x2             Settings opens on the tab bar; Confirm steps the tab,
                         so two of them land on Controls
  up -> confirm          `up` from the tab bar wraps to the last Controls row,
                         which is Bluetooth (SettingsActivity.cpp appends it)
  confirm                row 0 of BluetoothSettings is the Bluetooth toggle
  down -> confirm        row 1 is Scan & Pair
  confirm                row 0 of the scan list is the peer
  back x N -> confirm    out to Home, then Continue Reading = the reopen

Bluetooth is enabled from Settings with the book CLOSED, deliberately: toggling
it from the reader menu with the Bible open takes the heap-floor silent-restart
path, which reboots the device and throws away the state under test.

What can and cannot be seen about HID input here:

  * `[BLEUI]`/`[BLELC]` ring lines give the scan, the connect and the stack's
    heap cost in the firmware's own numbers.
  * This payload has NO default BLE key bindings -- MappedInputManager::pollBle
    resolves every key against SETTINGS.bleKeyMap and drops what it cannot find,
    and there is no [BLEIN] log in this build to say so. So a right-arrow turns
    a page only if a mapping exists. The SD image this runs against carries one
    in /.crosspoint/settings.json (right-arrow -> Button::Right), written the
    way the Map Remote Buttons UI would write it; SEED_SETTINGS below documents
    it. Without that seed the correct result is "the key arrived and was
    dropped", which is not what we are trying to measure.
  * `freeink::BleKeyboardHost::onReportIngest` and `bleinput::encodeKey` are
    traced as non-halting PC hooks, so every arriving report is timestamped on
    the same clock as the build's `Framebuffer lent` lines even when the key
    goes nowhere. A page turn additionally shows up as a `[SCT]`/render line.

Env: SIMANTIC_SIM, PORT, REPLX, PAYLOAD, EXTRA_SYMS, EXTRA_ARGS, HOME_ROW,
WATCH_V (virtual seconds to watch after the reopen, default 150).
"""
import socket, base64, os, json, time, re, subprocess, sys, pathlib
import guard
HERE = pathlib.Path(__file__).resolve().parent
SIM = os.environ.get("SIMANTIC_SIM", "sim")
PORT = int(os.environ.get("PORT", 21240))
REPLX = os.environ.get("REPLX", "board-featbt-main-hid.replx")
WATCH_V = float(os.environ.get("WATCH_V", "130"))
#: The mapping the SD image must already carry for a HID key to reach a page
#: turn on this payload. k=0 is SpecialKey, 7 is SpecialKey::Right; k=1 is a raw
#: HID usage, 0x4F is the right arrow. b=3 is MappedInputManager::Button::Right,
#: which ReaderUtils.h turns a page on. Written to /.crosspoint/settings.json.
SEED_SETTINGS = '{"bleKeyMap":[{"k":0,"v":7,"b":3},{"k":1,"v":79,"b":3}]}'
env = dict(os.environ)
# The engine writes the SD image back, so a run leaves behind its section cache
# AND bluetoothEnabled=true -- on which the Bluetooth row would toggle BT OFF and
# the resume would find a complete cache instead of a partial one. Restore the
# pristine image (bible.epub + state.json + the seeded bleKeyMap, no cache) so
# every run starts from the same device state.
BASE_IMG = HERE/"sdcard-hidrun-base.img"; RUN_IMG = HERE/"sdcard-hidrun.img"
REPLX = guard.pristine_platform(HERE/REPLX, BASE_IMG, RUN_IMG)
OUT = HERE/"output.txt"; OUT.unlink(missing_ok=True)
(HERE/"trace-reopen-hid.yaml").write_text(f"""machines:
  xteink:
    repl: {REPLX}
    elf: ../payloads/{os.environ.get('PAYLOAD', 'crosspoint-featble-sim.elf')}
    symbolsElfPath: ../../.pio/build/sim/firmware.elf
    controlMap: "back=saradc@0;confirm=saradc@1;left=saradc@2;right=saradc@3;up=saradc@4;down=saradc@5"
media:
  - type: ble
    connect: [xteink.radio, xteink.blepeer]
controlPort: {PORT}
""")
SYMS = ["app_main", "ReaderActivity::onEnter", "EpubReaderActivity::loadBook", "GfxRenderer::displayBuffer",
        "EpubReaderMenuActivity::render", "EpubReaderMenuActivity::activateIndex",
        "panic_abort", "abort", "esp_restart",
        # BLE lifecycle and the HID input path, as non-halting PC hooks
        "bleinput::ensureStarted", "bleinput::stop", "freeink::BleKeyboardHost::begin",
        "freeink::BleKeyboardHost::onReportIngest", "bleinput::encodeKey"]
SYMS += [x for x in os.environ.get("EXTRA_SYMS", "").split(",") if x]
cmd = [SIM, "--scenario", "trace-reopen-hid.yaml", "--timeout", "1800", "--show-renode-logs"]
for s in SYMS: cmd += ["--trace-symbol", s]
cmd += guard.observer_args()
p = subprocess.Popen(cmd, cwd=HERE, env=env, stdout=open(HERE/"trace-reopen-hid.log","w"), stderr=subprocess.STDOUT, start_new_session=True)
import signal, atexit
def stop():
    if p.poll() is None:
        try: os.killpg(p.pid, signal.SIGTERM)
        except ProcessLookupError: pass
        p.wait(timeout=30)
atexit.register(stop)
T0 = time.time()
def log(*a): print(f"[{time.time()-T0:6.1f}s]", *a, flush=True)
def ws():
    for _ in range(90):
        try: s = socket.create_connection(("localhost", PORT), timeout=5); break
        except OSError: time.sleep(1)  # wall-ok: host port
    else: sys.exit("control port never opened")
    key = base64.b64encode(os.urandom(16)).decode()
    s.send((f"GET /control HTTP/1.1\r\nHost: localhost:{PORT}\r\nUpgrade: websocket\r\n"
            f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
    s.recv(4096); s.settimeout(0.05); return s
def send(s, obj):
    d = json.dumps(obj).encode(); m = os.urandom(4)
    hdr = bytes([0x81, 0x80 | len(d)]) if len(d) < 126 else bytes([0x81, 0xFE]) + len(d).to_bytes(2,"big")
    s.send(hdr + m + bytes(b ^ m[i%4] for i,b in enumerate(d)))
def drain(s):
    try:
        while s.recv(1 << 20): pass
    except (socket.timeout, OSError): pass
TR = re.compile(r"^\[(\d+\.\d+)s\] \(TRACE\) (\S+)(.*)$")
TS = re.compile(r"^\[(\d+\.\d+)s\]")
vnow = 0.0
gate = guard.RatioGate(lambda: vnow)
seen = []
pos = 0
def poll():
    global pos, vnow
    if not OUT.exists(): return 0
    with open(OUT, "r", errors="replace") as f:
        f.seek(pos); chunk = f.read(); pos = f.tell()
    n = 0
    for l in chunk.splitlines():
        ts = TS.match(l)
        if ts: vnow = max(vnow, float(ts.group(1)))
        m = TR.match(l)
        if m: seen.append((float(m.group(1)), m.group(2), m.group(3).strip())); n += 1
    return n
def count(sym): return sum(1 for _, s, _ in seen if s.startswith(sym))
#: Wall-clock backstop on any single wait. Every `limit` below is in VIRTUAL
#: seconds -- an e-ink repaint costs 1-2 s of the guest's time whether the host
#: runs the guest at 1x or at 1/50th, and with the RTC-ring watchpoints on this
#: host runs it at about 1/50th. Wall-clock limits silently became sub-second
#: virtual limits at that speed, which turns "wait for the menu to repaint" into
#: "press down again immediately" and walks the wrong row.
WALL_CAP = float(os.environ.get("WALL_CAP", "18000"))
def wait_for(pred, what, limit):
    """`limit` is VIRTUAL seconds."""
    t0 = time.time(); v0 = vnow
    while vnow - v0 < limit and time.time() - t0 < WALL_CAP:
        poll(); drain(s)
        if pred(): log("saw", what, "at virtual", f"{vnow:.3f}s"); return True
        time.sleep(0.2)
    log("TIMEOUT waiting for", what, f"(virtual {vnow - v0:.1f}s of {limit})"); return False
def wait_quiet(sym, quiet, limit):
    """`quiet` and `limit` are both VIRTUAL seconds."""
    t0 = time.time(); poll(); v0 = vnow; n = count(sym); last = vnow if n else None
    while vnow - v0 < limit and time.time() - t0 < WALL_CAP:
        poll(); drain(s)
        if count(sym) != n: n = count(sym); last = vnow
        if last is not None and vnow - last > quiet: return True
        time.sleep(0.2)
    return False
pid = 0
HOLD_V = float(os.environ.get("HOLD_V", "0.35"))
def press(s, name, hold=None):
    global pid
    pid += 1; poll(); t0 = vnow
    send(s, {"press": name, "id": pid})
    w0 = time.time()
    while vnow - t0 < HOLD_V and time.time() - w0 < WALL_CAP:
        time.sleep(0.05); poll(); drain(s)
    send(s, {"release": name})
_ring_cache = (0, [])
def ring_all():
    """Every decoded ring line as (virtual time, text). Re-decoded from scratch;
    the ring is 16 slots so this is cheap next to the run itself."""
    txt = subprocess.run([sys.executable, str(HERE/"ring_from_memtrace.py"), str(OUT)],
                         capture_output=True, text=True).stdout
    out = []
    for l in txt.splitlines():
        mm = re.match(r"\[\s*(\d+\.\d+)s\] (.*)", l)
        if mm: out.append((float(mm.group(1)), mm.group(2)))
    return out
def ring_since(t): return [x for vt, x in ring_all() if vt >= t]
def uart_text(t):
    out = []
    for l in open(OUT, errors="replace"):
        mm = re.match(r"^\[(\d+\.\d+)s\] \(UART0\) (.*)$", l.rstrip())
        if mm and float(mm.group(1)) >= t:
            out.append(bytes(int(x, 16) for x in mm.group(2).split()).decode("ascii", "replace"))
    return "".join(out)
def wait_ring(pattern, what, limit, since=0.0):
    """Wait for a ring line matching `pattern`; return the line or None."""
    rx = re.compile(pattern); t0 = time.time(); v0 = vnow
    while vnow - v0 < limit and time.time() - t0 < WALL_CAP:
        poll(); drain(s)
        for l in ring_since(since):
            if rx.search(l): log("ring:", l[:120]); return l
        time.sleep(0.5)
    log("TIMEOUT waiting for ring", what); return None
def entered(since):
    """Activity names entered since virtual time `since`, in order."""
    return [re.sub(r".*Entering activity: ", "", l) for l in ring_since(since) if "Entering activity" in l]
def goto_activity(name, keys, limit=90):
    """Play `keys`, then say whether `name` is what the firmware entered."""
    t = vnow
    for k in keys:
        press(s, k)
        # let the repaint the press causes get started before the next one
        vk = vnow; w = time.time()
        while vnow - vk < 1.5 and time.time() - w < WALL_CAP:
            time.sleep(0.2); poll(); drain(s)
    ok = wait_for(lambda: any(e.startswith(name) for e in entered(t)), f"activity {name}", limit)
    return ok, entered(t)

s = ws(); log("connected")
wait_for(lambda: count("ReaderActivity::onEnter") >= 1, "reader opened", 600)
wait_for(lambda: count("EpubReaderActivity::loadBook") >= 1, "loadBook", 120)
p0 = count("GfxRenderer::displayBuffer")
wait_for(lambda: count("GfxRenderer::displayBuffer") > p0, "first page painted", 900)
wait_quiet("GfxRenderer::displayBuffer", quiet=6.0, limit=600)
log("reader settled at virtual", f"{vnow:.3f}")
t_first_open = 0.0

# ---- reader menu -> Go Home (identical to the BLE-off repro) ----------------
for attempt in range(3):
    press(s, "confirm", hold=0.6)
    if wait_for(lambda: count("EpubReaderMenuActivity::render") >= 1, "menu render", 25): break
else: sys.exit("menu never opened")
row = int(os.environ.get("HOME_ROW", "12"))
def goto_row(r):
    for i in range(r):
        n = count("EpubReaderMenuActivity::render"); press(s, "down")
        wait_for(lambda: count("EpubReaderMenuActivity::render") > n, f"menu render after down #{i+1}", 30)
    nact = count("EpubReaderMenuActivity::activateIndex"); press(s, "confirm")
    wait_for(lambda: count("EpubReaderMenuActivity::activateIndex") > nact, "activateIndex", 30)
    act = [a for _, sym, a in seen if sym.startswith("EpubReaderMenuActivity::activateIndex")]
    return int(re.search(r"a1=0x([0-9A-F]+)", act[-1]).group(1), 16), seen[-1][0]
for attempt in range(4):
    took, t_act = goto_row(row)
    verdict = "neither"
    while vnow - t_act < 3.0:
        poll(); drain(s)
        ent = [l for l in ring_since(t_act) if "Entering activity" in l]
        if ent: verdict = ent[0]; break
        time.sleep(0.2)
    log("row", took, "->", verdict[:60])
    if "Entering activity: Home" in verdict: break
    if verdict == "neither":
        wait_quiet("GfxRenderer::displayBuffer", quiet=2.0, limit=60)
        for a2 in range(3):
            press(s, "confirm", hold=0.6)
            if wait_for(lambda: any("Entering activity: EpubReaderMenu" in l for l in ring_since(t_act + 0.5)), "menu reopened", 25): break
        wait_quiet("EpubReaderMenuActivity::render", quiet=2.5, limit=60)
        row = took - 1
        continue
    press(s, "back")
    tb = vnow
    wait_for(lambda: any("Entering activity: EpubReaderMenu" in l for l in ring_since(tb)), "menu re-entered", 60)
    wait_quiet("EpubReaderMenuActivity::render", quiet=2.5, limit=60)
    row = took + (2 if "QrDisplay" in verdict else 1)
else: sys.exit("could not find Go Home")
wait_quiet("GfxRenderer::displayBuffer", quiet=2.0, limit=120)
log("at Home")

# ---- Home -> Settings -> Controls -> Bluetooth ------------------------------
# Settings is the last Home row, so one `up` wraps onto it.
ok, ent = goto_activity("Settings", ["up", "confirm"])
if not ok: sys.exit(f"never reached Settings from Home (entered: {ent})")
# On the tab bar Confirm steps the tab: Display -> Reader -> Controls. Then `up`
# from the tab bar wraps to the last row of Controls, which is Bluetooth.
ok, ent = goto_activity("BluetoothSettings", ["confirm", "confirm", "up", "confirm"], limit=120)
if not ok: sys.exit(f"never reached BluetoothSettings (entered: {ent})")
t_bt = vnow
# Free heap before the stack comes up, in the firmware's own numbers.
pre = [l for l in ring_all() if "maxAlloc=" in l or "heap=" in l][-3:]
log("heap lines just before the BT toggle:", pre)
def bt_is_on(since):
    """True if the last BLE lifecycle line since `since` says the stack is up."""
    last = [l for l in ring_since(since) if "[BLELC] started" in l or "[BLELC] stopped" in l]
    return bool(last) and "started" in last[-1]
press(s, "confirm")                       # row 0: the Bluetooth toggle -> ON
started = wait_ring(r"\[BLELC\] started", "BLE start", 60, since=t_bt)
# Row 1 is Scan & Pair. A dropped `down` leaves the selection on row 0, where the
# next confirm turns Bluetooth back OFF -- so check which of the two happened and
# recover, rather than pressing on into an unknown screen.
for attempt in range(4):
    tscan = vnow
    press(s, "down"); press(s, "confirm")
    if wait_ring(r"scan view: (begin|startScan)", "scan view", 45, since=tscan): break
    if not bt_is_on(t_bt):                # the confirm hit the toggle instead
        log("that confirm turned Bluetooth off; turning it back on")
        tre = vnow
        press(s, "confirm")
        wait_ring(r"\[BLELC\] started", "BLE restart", 60, since=tre)
else:
    log("never reached the scan view")
wait_ring(r"scan view: state scanning=\d+ devices=[1-9]", "a device in the scan list", 90, since=t_bt)
# Row 0 of the scan list is the peer.
conn = None
for attempt in range(4):
    tc = vnow
    press(s, "confirm")
    conn = wait_ring(r"\[BLELC\] connected|scan view: connect ", "the peer connecting", 45, since=tc)
    if conn: break
t_conn = vnow
log("connected?", conn)
# Let the link sit long enough for the repeating peer to send a page turn while
# the reader is NOT in front, as a positive control on the HID path itself.
n_ingest0 = count("freeink::BleKeyboardHost::onReportIngest")
t0 = time.time()
while vnow - t_conn < 20 and time.time() - t0 < WALL_CAP:
    poll(); drain(s); time.sleep(0.3)
log("HID reports ingested while on the BT screen:",
    count("freeink::BleKeyboardHost::onReportIngest") - n_ingest0,
    "| encodeKey:", count("bleinput::encodeKey"))

# ---- back to Home, then reopen the Bible ------------------------------------
for i in range(6):
    tb = vnow
    press(s, "back")
    if wait_for(lambda: any(e.startswith("Home") for e in entered(tb)), "Home", 45): break
else: sys.exit("never got back to Home")
wait_quiet("GfxRenderer::displayBuffer", quiet=2.0, limit=120)
log("at Home with BT enabled and the peer bonded; reopening the book")
if os.environ.get("LIGHT_BOOK"):
    # Step 5 of the coexistence question: the Bible never lets BLE back in, because
    # the reader's heap stays under the 80 KB start floor for the whole resumed
    # build. A 3 KB book paginates in one pass and leaves the heap high, so this is
    # where "does a HID key still turn a page" can actually be answered.
    # Home rows: Continue Reading, Browse Files, Recent Books, File Transfer,
    # Settings. We arrive with Settings (the last row) selected, so one `down`
    # wraps to row 0 and a second lands on Browse Files.
    ok, ent = goto_activity("FileBrowser", ["down", "down", "confirm"], limit=60)
    if not ok: sys.exit(f"never reached the file browser (entered: {ent})")
    t_light = vnow
    for attempt in range(6):
        ta = vnow
        press(s, "confirm")
        if wait_for(lambda: count("ReaderActivity::onEnter") >= 2, "a book opened", 45): break
        press(s, "down")
        wait_quiet("GfxRenderer::displayBuffer", quiet=2.0, limit=60)
    book = [l for l in ring_since(t_light) if ".epub" in l or "Loading" in l][:4]
    log("light book opened:", book)
    t_reopen = vnow
    log("watching the light book with BLE for", WATCH_V, "virtual seconds")
    t0 = time.time()
    while vnow - t_reopen < WATCH_V and time.time() - t0 < WALL_CAP:
        poll(); drain(s)
        if count("panic_abort") + count("abort") >= 1: break
        time.sleep(0.5)
    stop(); poll()
    print("\n=== LIGHT BOOK RESULT ===")
    print("abort/panic:", count("panic_abort"), count("abort"))
    print("HID reports ingested total:", count("freeink::BleKeyboardHost::onReportIngest"),
          "| after this open:", len([t for t, sym, _ in seen
              if sym.startswith("freeink::BleKeyboardHost::onReportIngest") and t >= t_reopen]))
    print("keys decoded total:", count("bleinput::encodeKey"))
    for vt, l in ring_all():
        if vt >= t_light and any(k in l for k in ("Framebuffer", "BLELC", "BLEUI", "Entering activity", "Rendered page", "Page ")):
            print(f"   [{vt:9.3f}s] {l[:140]}")
    print(guard.observer_note())
    sys.exit(gate.check())

# Home restores the row it was last on, and we arrive from Settings -- which is
# Home's LAST row -- so a bare confirm re-enters Settings. Continue Reading is
# row 0, one `down` away by wrap. Verify by what opens rather than assume:
# every wrong row is recoverable with `back`, an unregistered press is not
# distinguishable from a wrong row any other way.
t_reopen = vnow
for attempt in range(5):
    ta = vnow
    press(s, "confirm", hold=0.6)
    if wait_for(lambda: count("ReaderActivity::onEnter") >= 2, "reader re-entered", 40): break
    opened = [e for e in entered(ta) if not e.startswith("Home")]
    log("confirm at Home opened", opened or "nothing")
    if opened:                      # wrong row: back out to Home
        tb = vnow
        press(s, "back")
        wait_for(lambda: any(e.startswith("Home") for e in entered(tb)), "Home again", 60)
    wait_quiet("GfxRenderer::displayBuffer", quiet=2.0, limit=60)
    press(s, "down")                # step one row (wraps onto Continue Reading)
    wait_quiet("GfxRenderer::displayBuffer", quiet=2.0, limit=60)
else:
    guard.require(False, "never reopened the book from Home")
t_reopen = vnow
log("watching the resumed build for", WATCH_V, "virtual seconds")
t0 = time.time()
while vnow - t_reopen < WATCH_V and time.time() - t0 < WALL_CAP:
    poll(); drain(s)
    if count("panic_abort") + count("abort") + count("esp_restart") >= 1: break
    if "abort() was called" in uart_text(t_reopen): break
    time.sleep(0.5)
stop(); poll()

# ---- report ------------------------------------------------------------------
print("\n=== RESULT ===")
print(guard.observer_note())
print("abort/panic hits:", count("panic_abort"), count("abort"), "| esp_restart:", count("esp_restart"))
print("BLE: ensureStarted", count("bleinput::ensureStarted"), "| host begin", count("freeink::BleKeyboardHost::begin"),
      "| stop", count("bleinput::stop"))
print("HID: reports ingested", count("freeink::BleKeyboardHost::onReportIngest"),
      "| keys decoded", count("bleinput::encodeKey"))
ing_after = [t for t, sym, _ in seen if sym.startswith("freeink::BleKeyboardHost::onReportIngest") and t >= t_reopen]
print("HID reports AFTER the reopen:", len(ing_after), "at virtual", [f"{t:.1f}" for t in ing_after[:40]])
u = uart_text(t_reopen)
print("UART0 panic text:", "YES" if "abort() was called" in u else "no")
print("--- every Framebuffer / BLE / cache ring line after the reopen:")
for vt, l in ring_all():
    if vt < t_reopen: continue
    if any(k in l for k in ("Framebuffer", "BLELC", "BLEUI", "Partial cache", "resuming", "maxAlloc")):
        print(f"   [{vt:9.3f}s] {l[:150]}")
print("--- ring after reopen (last 40 non-page lines):")
for l in [l for l in ring_since(t_reopen) if "[SCT] Page" not in l and "FDC] Prewarm" not in l][-40:]:
    print("  ", l[:150])
if "abort() was called" in u:
    print("--- panic dump (first 14 lines):")
    for l in u.splitlines()[:14]: print("  ", l[:120])
    import glob
    a2l = glob.glob(os.path.expanduser("~/.platformio/packages/toolchain-riscv32-esp*/bin/riscv32-esp-elf-addr2line"))[0]
    elf = str(HERE.parent.parent/".pio"/"build"/"sim"/"firmware.elf")
    addrs = []
    m = re.search(r"abort\(\) was called at PC (0x[0-9a-f]+)", u)
    if m: addrs.append(m.group(1))
    addrs += [a for a in re.findall(r"0x42[0-9a-f]{6}", u.split("Stack memory:")[1] if "Stack memory:" in u else "")]
    print("--- symbolized (PC, then text-range words from the stack dump in order):")
    out = subprocess.run([a2l, "-f", "-C", "-e", elf] + addrs, capture_output=True, text=True).stdout.splitlines()
    for i in range(0, len(out), 2):
        print(f"   {addrs[i//2]}  {out[i][:90]}  {out[i+1].split('/')[-1][:50]}")
sys.exit(gate.check())
