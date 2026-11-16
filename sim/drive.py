#!/usr/bin/env python3
"""Presses the X4 front buttons through `sim --control-port`: drive.py PORT 'btn:wait_s,btn:wait_s,...'.
The ladder is on ADC1 ch1 (GPIO1: back/confirm/left/right) and ch2 (GPIO2: up/down), volts at the pin.
MODELED SIMPLIFICATION: the volts per button are the middle of the firmware's ADC bands, not a measured ladder."""
import sys, time
sys.path.insert(0, __import__("os").path.dirname(__file__))
import ctl

IDLE = 3.3
BTN = {"back": (1, 2.83), "confirm": (1, 2.17), "left": (1, 1.2), "right": (1, 0), "up": (2, 1.81), "down": (2, 0)}

def setch(s, ch, v):
    ctl.send(s, '{"set":{"peripheral":"saradc","property":"ch%d","value":%s}}' % (ch, v))
    r = ctl.recv(s)
    if '"error"' in r: print(r, flush=True)

s = ctl.connect(int(sys.argv[1]))
setch(s, 1, IDLE); setch(s, 2, IDLE)
for step in sys.argv[2].split(","):
    name, wait = step.split(":")
    if name != "wait":
        ch, v = BTN[name]; setch(s, ch, v); time.sleep(0.4); setch(s, ch, IDLE)
        print("pressed", name, flush=True)
    time.sleep(float(wait))
