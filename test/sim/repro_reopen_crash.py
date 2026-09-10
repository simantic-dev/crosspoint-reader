"""Reproduce the bad_alloc abort in the resumed section build: open the book,
Go Home from the reader menu, reopen the book, wait.  Paced by --trace-symbol
lines and the RTC ring (via --trace-memory) instead of sleeps.

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
(HERE/"trace-reopen.yaml").write_text(f"""machines:
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
        "panic_abort", "abort", "esp_restart"]
SYMS += [x for x in os.environ.get("EXTRA_SYMS", "").split(",") if x]
cmd = [SIM, "--scenario", "trace-reopen.yaml", "--timeout", "900", "--show-renode-logs"]
for s in SYMS: cmd += ["--trace-symbol", s]
cmd += [a for a in os.environ.get("EXTRA_ARGS", "--memory-stats 100ms --trace-memory logHead --trace-memory logMessages:4096").split() if a]   # e.g. "--trace-memory logHead --trace-memory logMessages:4096"
p = subprocess.Popen(cmd, cwd=HERE, env=env, stdout=open(HERE/"trace-reopen.log","w"), stderr=subprocess.STDOUT, start_new_session=True)
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
TS = re.compile(r"^\[(\d+\.\d+)s\]")
vnow = 0.0                                  # virtual time of the newest line seen
seen = []            # (vt, symbol, args)
pos = 0
def poll():
    """Read new trace lines from output.txt; return how many arrived."""
    global pos
    if not OUT.exists(): return 0
    with open(OUT, "r", errors="replace") as f:
        f.seek(pos); chunk = f.read(); pos = f.tell()
    global vnow
    n = 0
    for l in chunk.splitlines():
        ts = TS.match(l)
        if ts: vnow = max(vnow, float(ts.group(1)))
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
    """Wait until `sym` has been hit at least once and then stops arriving for `quiet` VIRTUAL seconds."""
    t0 = time.time(); poll(); n = count(sym); last = vnow if n else None
    while time.time() - t0 < limit:
        poll(); drain(s)
        if count(sym) != n: n = count(sym); last = vnow
        if last is not None and vnow - last > quiet: return True
        time.sleep(0.2)
    return False
pid = 0
HOLD_V = float(os.environ.get("HOLD_V", "0.35"))   # virtual seconds a button stays down
def press(s, name, hold=None):
    """Press, hold until the sim's own clock has advanced HOLD_V, release.
    The clock is the newest timestamp in output.txt; --memory-stats gives a
    line every 0.1 s virtual so the hold never depends on host speed."""
    global pid
    pid += 1; poll(); t0 = vnow
    send(s, {"press": name, "id": pid})
    w0 = time.time()
    while vnow - t0 < HOLD_V and time.time() - w0 < 60:
        time.sleep(0.05); poll(); drain(s)
    send(s, {"release": name})

def ring_since(t):
    import subprocess
    txt = subprocess.run([sys.executable, str(HERE/"ring_from_memtrace.py"), str(OUT)], capture_output=True, text=True).stdout
    out = []
    for l in txt.splitlines():
        mm = re.match(r"\[\s*(\d+\.\d+)s\] (.*)", l)
        if mm and float(mm.group(1)) >= t: out.append(mm.group(2))
    return out
def uart_text(t):
    out = []
    for l in open(OUT, errors="replace"):
        mm = re.match(r"^\[(\d+\.\d+)s\] \(UART0\) (.*)$", l.rstrip())
        if mm and float(mm.group(1)) >= t:
            out.append(bytes(int(x, 16) for x in mm.group(2).split()).decode("ascii", "replace"))
    return "".join(out)
s = ws(); log("connected")
wait_for(lambda: count("ReaderActivity::onEnter") >= 1, "reader opened", 600)
wait_for(lambda: count("EpubReaderActivity::loadBook") >= 1, "loadBook", 120)
p0 = count("GfxRenderer::displayBuffer")
wait_for(lambda: count("GfxRenderer::displayBuffer") > p0, "first page painted", 900)
wait_quiet("GfxRenderer::displayBuffer", quiet=6.0, limit=600)
log("reader settled at virtual", f"{vnow:.3f}")
if os.environ.get("NO_HOME"):
    # control: never leave the reader; let the first, unsuspended build run to
    # its end (or its abort) and report the same way
    t_reopen = vnow
    log("NO_HOME: waiting for the first build to finish or abort")
    t0 = time.time()
    while time.time() - t0 < 1200:
        poll(); drain(s)
        if count("panic_abort") + count("abort") + count("esp_restart") >= 1: break
        if "abort() was called" in uart_text(t_reopen): break
        if any("build complete" in l.lower() or "Section build finished" in l for l in ring_since(t_reopen)): break
        time.sleep(0.5)
else:
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
          # the row popped the menu without opening anything (Sync, Delete cache):
          # we are back in the reader; reopen the menu and try one row higher
          wait_quiet("GfxRenderer::displayBuffer", quiet=2.0, limit=60)
          for a2 in range(3):
              press(s, "confirm", hold=0.6)
              if wait_for(lambda: any("Entering activity: EpubReaderMenu" in l for l in ring_since(t_act + 0.5)), "menu reopened", 25): break
          wait_quiet("EpubReaderMenuActivity::render", quiet=2.5, limit=60)
          row = took - 1
          continue
      if "[ERM]" in verdict or any("[ERM]" in l or "[BLELC]" in l for l in ring_since(t_act)):
          sys.exit("hit the Bluetooth toggle instead of Go Home; aborting to keep BLE out of this experiment")
      press(s, "back")
      tb = vnow
      wait_for(lambda: any("Entering activity: EpubReaderMenu" in l for l in ring_since(tb)), "menu re-entered", 60)
      wait_quiet("EpubReaderMenuActivity::render", quiet=2.5, limit=60)
      row = took + (2 if "QrDisplay" in verdict else 1)
  else: sys.exit("could not find Go Home")
  t_home = vnow
  wait_quiet("GfxRenderer::displayBuffer", quiet=2.0, limit=120)
  log("at Home; reopening the recent book")
  press(s, "confirm", hold=0.6)
  t_reopen = vnow
  wait_for(lambda: count("ReaderActivity::onEnter") >= 2, "reader re-entered", 120)
  log("waiting for the resumed build to finish or abort")
  t0 = time.time()
  while time.time() - t0 < 900:
      poll(); drain(s)
      if count("panic_abort") + count("abort") + count("esp_restart") >= 1: break
      if "abort() was called" in uart_text(t_reopen): break
      if any("Section build complete" in l or "build complete" in l.lower() for l in ring_since(t_reopen)): break
      time.sleep(0.5)
stop(); poll()
print("\n=== RESULT ===")
print("abort/panic hits:", count("panic_abort"), count("abort"), "| esp_restart:", count("esp_restart"))
u = uart_text(t_reopen)
print("UART0 panic text:", "YES" if "abort() was called" in u else "no")
print("--- ring after reopen (last 40 non-page lines):")
for l in [l for l in ring_since(t_reopen) if "[SCT] Page" not in l and "FDC] Prewarm" not in l][-40:]: print("  ", l[:150])
if "abort() was called" in u:
    print("--- panic dump (first 12 lines):")
    for l in u.splitlines()[:12]: print("  ", l[:120])
    import glob, subprocess
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
