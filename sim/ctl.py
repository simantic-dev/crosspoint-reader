#!/usr/bin/env python3
"""Minimal stdlib WebSocket client for `sim --control-port`: ctl.py PORT JSON [JSON...] prints each reply."""
import base64, json, os, socket, struct, sys, time

def connect(port):
    for _ in range(100):
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=5); break
        except OSError:
            time.sleep(0.2)
    key = base64.b64encode(os.urandom(16)).decode()
    s.sendall(f"GET /control HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n".encode())
    buf = b""
    while b"\r\n\r\n" not in buf: buf += s.recv(1)
    return s

def send(s, text):
    d = text.encode(); m = os.urandom(4)
    h = bytes([0x81]) + (bytes([0x80 | len(d)]) if len(d) < 126 else bytes([0xFE]) + struct.pack(">H", len(d))) + m
    s.sendall(h + bytes(b ^ m[i % 4] for i, b in enumerate(d)))

def recv(s):
    h = s.recv(2); n = h[1] & 0x7F
    if n == 126: n = struct.unpack(">H", s.recv(2))[0]
    d = b""
    while len(d) < n: d += s.recv(n - len(d))
    return d.decode()

if __name__ == "__main__":
    s = connect(int(sys.argv[1]))
    for j in sys.argv[2:]:
        send(s, j); print(recv(s), flush=True)
