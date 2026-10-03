#!/usr/bin/env python3
"""glowline server — ambient lid-glow for AI chat state.

Stdlib only (no pip install). Listens on ws://localhost:8765 for JSON state
messages from the Glowline browser extension and paints a fullscreen,
borderless, always-on-top window whose color reflects aggregate chat state:

    BUSY    -> pulsing amber      (a model is generating)
    WAITING -> solid soft emerald (response done, composer ready)
    ERROR   -> pulsing red        (network failure / rate limit)
    IDLE    -> dim slate          (nothing happening)

Messages look like: {"site": "chatgpt", "state": "BUSY", "ts": 1727860000000}
State is tracked per site and aggregated by priority
ERROR > BUSY > WAITING > IDLE. Sites that go quiet for STALE_AFTER seconds
are dropped back to IDLE (tab closed, laptop slept, etc).

Shortcuts (window focused):
    Esc          hide / show the glow window
    click        hide / show the glow window
    Cmd+Esc      quit
    Cmd+Q        quit

Run:  python3 server/glowline.py
Note: needs tkinter. python.org macOS builds ship it; Homebrew python needs
`brew install python-tk@<version>`.
"""

import base64
import hashlib
import json
import math
import socket
import struct
import threading
import time
import tkinter as tk

PORT = 8765
STALE_AFTER = 45  # seconds without a message before a site is forgotten

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

# (r, g, b) targets per state
COLORS = {
    "BUSY": (255, 149, 0),     # deep amber
    "WAITING": (52, 211, 153),  # soft emerald
    "ERROR": (255, 59, 48),     # red
    "IDLE": (13, 18, 27),       # dim slate
}
PRIORITY = {"ERROR": 3, "BUSY": 2, "WAITING": 1, "IDLE": 0}


# ---------------------------------------------------------------- websocket
def _recvall(conn, n):
    buf = b""
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("peer closed")
        buf += chunk
    return buf


def _handshake(conn):
    req = b""
    while b"\r\n\r\n" not in req:
        chunk = conn.recv(4096)
        if not chunk:
            raise ConnectionError("no handshake")
        req += chunk
    headers = {}
    for line in req.decode("latin1").split("\r\n")[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    key = headers.get("sec-websocket-key")
    if not key:
        raise ConnectionError("missing Sec-WebSocket-Key")
    accept = base64.b64encode(
        hashlib.sha1((key + WS_GUID).encode()).digest()
    ).decode()
    conn.sendall(
        (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
        ).encode()
    )


def _read_frame(conn):
    """Returns (opcode, payload_bytes)."""
    b1, b2 = _recvall(conn, 2)
    opcode = b1 & 0x0F
    length = b2 & 0x7F
    if length == 126:
        length = struct.unpack(">H", _recvall(conn, 2))[0]
    elif length == 127:
        length = struct.unpack(">Q", _recvall(conn, 8))[0]
    mask = _recvall(conn, 4) if (b2 & 0x80) else None
    payload = _recvall(conn, length) if length else b""
    if mask:
        payload = bytes(c ^ mask[i % 4] for i, c in enumerate(payload))
    return opcode, payload


def _send_frame(conn, opcode, payload=b""):
    header = bytes([0x80 | opcode])
    n = len(payload)
    if n < 126:
        header += bytes([n])
    elif n < 65536:
        header += struct.pack(">BH", 126, n)
    else:
        header += struct.pack(">BQ", 127, n)
    conn.sendall(header + payload)  # server -> client frames are unmasked


class GlowState:
    """Thread-safe per-site state with priority aggregation + staleness."""

    def __init__(self):
        self._lock = threading.Lock()
        self._sites = {}  # site -> (state, last_seen_monotonic)

    def update(self, site, state):
        if state not in PRIORITY:
            return
        with self._lock:
            self._sites[site] = (state, time.monotonic())

    def snapshot(self):
        """Returns (aggregate_state, hottest_site)."""
        now = time.monotonic()
        best, best_site = "IDLE", None
        with self._lock:
            for site in list(self._sites):
                state, seen = self._sites[site]
                if now - seen > STALE_AFTER:
                    del self._sites[site]
                    continue
                if PRIORITY[state] > PRIORITY[best]:
                    best, best_site = state, site
        return best, best_site


def _client_loop(conn, addr, glow):
    try:
        _handshake(conn)
        while True:
            opcode, payload = _read_frame(conn)
            if opcode == 0x8:  # close
                _send_frame(conn, 0x8, payload)
                break
            if opcode == 0x9:  # ping
                _send_frame(conn, 0xA, payload)
                continue
            if opcode == 0x1:  # text
                try:
                    msg = json.loads(payload.decode("utf-8", "replace"))
                except (ValueError, UnicodeDecodeError):
                    continue
                if isinstance(msg, dict) and msg.get("site") and msg.get("state"):
                    glow.update(str(msg["site"]), str(msg["state"]))
    except (ConnectionError, OSError):
        pass
    finally:
        try:
            conn.close()
        except OSError:
            pass


def serve_forever(glow):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        srv.bind(("127.0.0.1", PORT))
    except OSError:
        print(f"!! port {PORT} is already in use — is glowline already running?")
        raise SystemExit(1)
    srv.listen(8)
    print(f"glowline listening on ws://localhost:{PORT}")
    while True:
        conn, addr = srv.accept()
        threading.Thread(
            target=_client_loop, args=(conn, addr, glow), daemon=True
        ).start()


# ------------------------------------------------------------------- display
def _hex(rgb):
    r, g, b = (max(0, min(255, int(c))) for c in rgb)
    return f"#{r:02x}{g:02x}{b:02x}"


class GlowWindow:
    def __init__(self, glow):
        self.glow = glow
        self.root = tk.Tk()
        self.root.title("glowline")
        self.root.attributes("-fullscreen", True)
        self.root.attributes("-topmost", True)
        self.root.overrideredirect(True)
        self.root.configure(background="black")

        w = self.root.winfo_screenwidth()
        h = self.root.winfo_screenheight()
        self.canvas = tk.Canvas(
            self.root, width=w, height=h, highlightthickness=0, bd=0
        )
        self.canvas.pack(fill="both", expand=True)
        self.rect = self.canvas.create_rectangle(0, 0, w, h, fill="#0d121b", outline="")
        self.label = self.canvas.create_text(
            w - 24, h - 20, text="", anchor="se",
            fill="#5b6472", font=("SF Mono", 11),
        )

        self.hidden = False
        self.cur = [float(c) for c in COLORS["IDLE"]]

        self.root.bind("<Escape>", lambda _e: self.toggle())
        self.root.bind("<Command-Escape>", lambda _e: self.quit())
        self.root.bind("<Command-q>", lambda _e: self.quit())
        self.root.bind("<Button-1>", lambda _e: self.toggle())

        self.tick()

    def toggle(self):
        if self.hidden:
            self.root.deiconify()
            self.root.attributes("-fullscreen", True)
            self.root.attributes("-topmost", True)
            self.hidden = False
        else:
            self.root.withdraw()
            self.hidden = True

    def quit(self):
        self.root.destroy()

    def tick(self):
        state, site = self.glow.snapshot()
        target = COLORS[state]

        # pulse the "active" states; solid for the rest
        t = time.monotonic()
        if state == "BUSY":
            pulse = 0.55 + 0.45 * (0.5 + 0.5 * math.sin(t * 2 * math.pi / 1.4))
            target = tuple(c * pulse for c in target)
        elif state == "ERROR":
            pulse = 0.50 + 0.50 * (0.5 + 0.5 * math.sin(t * 2 * math.pi / 2.0))
            target = tuple(c * pulse for c in target)

        # smooth crossfade toward the target
        self.cur = [c + (tg - c) * 0.18 for c, tg in zip(self.cur, target)]
        self.canvas.itemconfig(self.rect, fill=_hex(self.cur))
        self.canvas.itemconfig(
            self.label, text=f"{site or '—'} · {state}" if site else state
        )
        self.root.after(33, self.tick)  # ~30fps

    def run(self):
        self.root.mainloop()


def main():
    glow = GlowState()
    threading.Thread(target=serve_forever, args=(glow,), daemon=True).start()
    print("glowline glow window starting — Esc hides, Cmd+Esc quits")
    GlowWindow(glow).run()


if __name__ == "__main__":
    main()
