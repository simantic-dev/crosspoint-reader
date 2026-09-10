"""Toggle Bluetooth from the reader menu and watch what the firmware does, paced by
--trace-symbol lines instead of sleeps.  Also answers whether the CLI survives
CrossPoint's software reset (a second app_main hit).

Observability is --trace-symbol (non-halting PC hooks, virtual-time stamped,
written to output.txt interleaved with the UART rows):
  ReaderActivity::onEnter               reader opened
  GfxRenderer::displayBuffer            a repaint
  EpubReaderMenuActivity::render        menu is up
  EpubReaderMenuActivity::activateIndex a1 = the row the confirm activated
  silentRestartToReader / esp_restart   the firmware asked for the reset
  app_main (2nd hit)                    the machine rebooted and ran again
No sleeps for pacing: every step waits for the trace line that proves the
previous one landed.  Presses go over the control WebSocket with ids.
"""
import socket, base64, os, json, time, re, subprocess, sys, pathlib
HERE = pathlib.Path(__file__).resolve().parent
SIM = os.environ.get("SIMANTIC_SIM", "sim")   # needs a CLI with symbolsElfPath support
PORT = int(os.environ.get("PORT", 21239)); BT_ROW = int(os.environ.get("BT_ROW", 11))
REPLX = os.environ.get("REPLX", "board-featbt-f16-32k.replx")
env = dict(os.environ)
OUT = HERE/"output.txt"; OUT.unlink(missing_ok=True)
(HERE/"trace-toggle.yaml").write_text(f"""machines:
  xteink:
    repl: {REPLX}
    elf: ../payloads/crosspoint-featble-sim.elf
    symbolsElfPath: ../../.pio/build/sim/firmware.elf
    controlMap: "back=saradc@0;confirm=saradc@1;left=saradc@2;right=saradc@3;up=saradc@4;down=saradc@5"
media:
  - type: ble
    connect: [xteink.radio, xteink.blepeer]
controlPort: {PORT}
""")
SYMS = ["app_main", "ReaderActivity::onEnter", "EpubReaderActivity::loadBook", "GfxRenderer::displayBuffer",
        "EpubReaderMenuActivity::render", "EpubReaderMenuActivity::activateIndex",
        "silentRestartToReader", "esp_restart",
        "bleinput::ensureStarted", "bleinput::stop", "freeink::BleKeyboardHost::begin", "logPrintf"]
cmd = [SIM, "--scenario", "trace-toggle.yaml", "--timeout", "900", "--show-renode-logs"]
for s in SYMS: cmd += ["--trace-symbol", s]
p = subprocess.Popen(cmd, cwd=HERE, env=env, stdout=open(HERE/"trace-toggle.log","w"), stderr=subprocess.STDOUT, start_new_session=True)
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
        except OSError: time.sleep(1)
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
seen = []            # (vt, symbol, args)
pos = 0
def poll():
    """Read new trace lines from output.txt; return how many arrived."""
    global pos
    if not OUT.exists(): return 0
    with open(OUT, "r", errors="replace") as f:
        f.seek(pos); chunk = f.read(); pos = f.tell()
    n = 0
    for l in chunk.splitlines():
        m = TR.match(l)
        if m: seen.append((float(m.group(1)), m.group(2), m.group(3).strip())); n += 1
    return n
def count(sym): return sum(1 for _, s, _ in seen if s.startswith(sym))
def wait_for(pred, what, limit):
    t0 = time.time()
    while time.time() - t0 < limit:
        poll(); drain(s)
        if pred(): log("saw", what, "at virtual", f"{seen[-1][0]:.3f}s" if seen else "?"); return True
        time.sleep(0.2)
    log("TIMEOUT waiting for", what); return False
def wait_quiet(sym, quiet, limit):
    """Wait until `sym` has been hit at least once and then stops arriving for `quiet` wall-seconds."""
    t0 = time.time(); poll(); n = count(sym); last = time.time() if n else None
    while time.time() - t0 < limit:
        poll(); drain(s)
        if count(sym) != n: n = count(sym); last = time.time()
        if last and time.time() - last > quiet: return True
        time.sleep(0.2)
    return False
pid = 0
def press(s, name, hold=0.3):
    global pid
    pid += 1; send(s, {"press": name, "id": pid}); time.sleep(hold); send(s, {"release": name})
s = ws(); log("connected")
ok = wait_for(lambda: count("ReaderActivity::onEnter") >= 1, "ReaderActivity::onEnter (reader opened)", 600)
if not ok: log("no trace lines at all? lines seen:", len(seen)); 
wait_for(lambda: count("EpubReaderActivity::loadBook") >= 1, "loadBook entry", 120)
paints0 = count("GfxRenderer::displayBuffer")
wait_for(lambda: count("GfxRenderer::displayBuffer") > paints0, "first page painted after loadBook", 900)
wait_quiet("GfxRenderer::displayBuffer", quiet=6.0, limit=600)      # background build repaints stopped
log("reader settled; repaints so far", count("GfxRenderer::displayBuffer"))
for attempt in range(3):
    press(s, "confirm", hold=0.6)
    if wait_for(lambda: count("EpubReaderMenuActivity::render") >= 1, "menu render", 25): break
else: sys.exit("menu never opened after 3 confirms")
for i in range(BT_ROW):
    # A menu repaint is the only proof a down registered; background section
    # builds also repaint, so count the menu's own render, and retry a press
    # that produced none (ADC presses can be missed).
    for attempt in range(3):
        n = count("EpubReaderMenuActivity::render"); press(s, "down", hold=0.6)
        if wait_for(lambda: count("EpubReaderMenuActivity::render") > n, f"menu render after down #{i+1}", 12): break
    else: sys.exit(f"down #{i+1} never registered")
log("pressing confirm -> expect activateIndex a1=%d then silentRestartToReader" % BT_ROW)
press(s, "confirm")
wait_for(lambda: count("EpubReaderMenuActivity::activateIndex") >= 1, "activateIndex", 30)
act = [a for _, sym, a in seen if sym.startswith("EpubReaderMenuActivity::activateIndex")]
log("activateIndex args:", act[-1] if act else None)
row = int(re.search(r"a1=0x([0-9A-F]+)", act[-1]).group(1), 16) if act else -1
if row != BT_ROW: sys.exit(f"activated row {row}, wanted {BT_ROW}: a press was dropped or the menu differs; not proceeding blind")
if not (count("silentRestartToReader") + count("esp_restart")):
    # Heap was above the floor: the toggle only flipped the setting. BLE starts from
    # the lifecycle tick once the reader is back in front, so leave the menu.
    log("no inline restart; pressing back to return to the reader")
    nb = count("GfxRenderer::displayBuffer"); press(s, "back", hold=0.6)
    wait_for(lambda: count("GfxRenderer::displayBuffer") > nb, "repaint after back (reader in front)", 60)
wait_for(lambda: count("silentRestartToReader") + count("esp_restart") + count("bleinput::ensureStarted") + count("freeink::BleKeyboardHost::begin") >= 1, "restart request or BLE start", 300)
log("BLE starts:", count("bleinput::ensureStarted"), "| host begin:", count("freeink::BleKeyboardHost::begin"))
if not (count("silentRestartToReader") + count("esp_restart")):
    log("no restart requested: this state does not take the defrag-restart path; stopping early")
    stop(); poll()
if count("silentRestartToReader") + count("esp_restart"): wait_for(lambda: count("app_main") >= 2, "second app_main (machine rebooted)", 180)
if count("app_main") >= 2: wait_for(lambda: count("ReaderActivity::onEnter") >= 2, "reader re-opened after reboot", 300)
stop(); poll()
print("\n=== RESULT ===")
print("app_main hits:", count("app_main"), "| restart requests:", count("silentRestartToReader"), "+", count("esp_restart"),
      "| reader opens:", count("ReaderActivity::onEnter"), "| repaints:", count("GfxRenderer::displayBuffer"))
raw = OUT.read_text(errors="replace")
print("ROM banners on UART0:", raw.count("(UART0) 45 53 50 2D 52 4F 4D 3A"))
print("trace timeline (first 3 + all non-repaint):")
shown = 0
for vt, sym, a in seen:
    if sym.startswith("GfxRenderer::displayBuffer") or sym.startswith("logPrintf"):
        shown += 1
        if shown > 3: continue
    print(f"  {vt:9.3f}s {sym} {a[:60]}")
