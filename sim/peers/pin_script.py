# Time-scripted GPIO stimulus: a bus-less `Scripted.ScriptedGpioPeer` that drives its output lines to
# levels at chosen virtual times. Use it for button presses, a switch held for N ms, a shutdown-circuit or
# power-good level that changes mid-run, a fault line that glitches.
#
# Wiring is the board file's: output line n of this peer is `n -> <port>@<pin>` under the peer entry, and
# `config:` carries the script as JSON (an overlay string, so quotes are escaped; see
# tests/nxp/teensy41/gpio-script/peers.board). Input lines (`<port>: <pin> -> peer@n`) are logged only.
#
# config keys:
#   "idle":   {"<line>": level}   level before the first event; default is released (the pad's pull decides).
#   "events": [{"at_ms": 100, "line": 0, "level": 0}, ...]
#   "holds":  [{"line": 0, "at_ms": 100, "for_ms": 200, "level": 0}, ...]
#             drives `level` at at_ms, then returns the line to its idle state after for_ms (a press).
# A level is 1 / 0 (or true / false) to drive, or "release" / null to stop driving (open drain: the pad
# reads its pull-up or pull-down). "at_us" / "for_us" may be used in place of the _ms keys.
# Events at the same time apply in the order given, "events" before "holds".
#
# Not modelled: contact bounce (write the bounces as extra events), edge slew, drive strength.
RELEASE = "release"


def _us(d, key):
    return int(round(d[key + "_us"] if key + "_us" in d else d[key + "_ms"] * 1000))


def _level(v):
    return None if v is None or v == RELEASE else bool(v)


def build_schedule(cfg):
    """Sorted [(t_us, line, level)] with level True / False / None (release)."""
    idle = {int(k): _level(v) for k, v in cfg.get("idle", {}).items()}
    sched = []
    for e in cfg.get("events", []):
        sched.append((_us(e, "at"), int(e["line"]), _level(e["level"])))
    for h in cfg.get("holds", []):
        line, t = int(h["line"]), _us(h, "at")
        sched.append((t, line, _level(h["level"])))
        sched.append((t + _us(h, "for"), line, idle.get(line)))
    return idle, sorted(sched, key=lambda s: s[0])


class Slave:
    def __init__(self, ctx):
        self.ctx = ctx
        self.idle, self.sched = build_schedule(getattr(ctx, "config", {}))
        self.seen = []
        for line, level in self.idle.items():
            self._drive(line, level)
        self._arm()

    def _drive(self, line, level):
        pin = self.ctx.gpio(line)
        pin.release() if level is None else pin.set(level)

    def _arm(self):
        if self.sched:
            self.ctx.schedule_oneshot(max(1, self.sched[0][0] - self.ctx.now_us))

    def on_timer(self):
        now = self.ctx.now_us
        while self.sched and self.sched[0][0] <= now:
            _, line, level = self.sched.pop(0)
            self._drive(line, level)
        self._arm()

    def on_gpio(self, line, level):
        self.seen.append((self.ctx.now_us, line, bool(level)))
