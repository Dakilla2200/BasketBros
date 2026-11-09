#!/usr/bin/env python3
"""
TapTask - a TinyTask-style record/replay auto clicker for iPhone.

iOS does not let one app tap inside another, so this drives the phone from a
computer through WebDriverAgent (WDA), the same agent Appium uses. You get a
live mirror of the phone in your browser; every click/drag you make on the
mirror is sent to the phone AND recorded with its timing. Hit Play to replay
it (loops, speed multiplier), or use the classic fixed-interval auto click.

Pure standard library. Python 3.8+.

    python3 autoclicker.py                       # UI at http://localhost:8765
    python3 autoclicker.py --wda http://192.168.1.20:8100
    python3 autoclicker.py play macro.json --loops 0 --speed 2   # headless
"""
import argparse
import base64
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent


# --------------------------------------------------------------------------- WDA
class WDA:
    def __init__(self, base):
        self.base = base.rstrip("/")
        self.sid = None
        self.size = None  # (w, h) in points
        self.lock = threading.Lock()

    def _req(self, method, path, body=None, timeout=30):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read() or b"{}")
            except ValueError:
                raise RuntimeError(f"WDA HTTP {e.code} on {path}") from None

    def session(self, fresh=False):
        with self.lock:
            if self.sid and not fresh:
                return self.sid
            r = self._req("POST", "/session", {"capabilities": {"alwaysMatch": {}}})
            sid = r.get("sessionId") or (r.get("value") or {}).get("sessionId")
            if not sid:
                raise RuntimeError(f"Could not open WDA session: {r}")
            self.sid = sid
            v = self._req("GET", f"/session/{sid}/window/size")["value"]
            self.size = (float(v["width"]), float(v["height"]))
            return sid

    def status(self):
        return self._req("GET", "/status", timeout=5)

    def screenshot_png(self):
        return base64.b64decode(self._req("GET", "/screenshot")["value"])

    def _pts(self, fx, fy):
        w, h = self.size
        return round(fx * w), round(fy * h)

    def perform(self, a):
        """a = {kind: tap|hold|swipe, x, y, [x2, y2], [ms]} with x/y as 0..1 fractions."""
        sid = self.session()
        x, y = self._pts(a["x"], a["y"])
        kind = a.get("kind", "tap")
        ms = int(a.get("ms", 0))
        steps = [{"type": "pointerMove", "duration": 0, "x": x, "y": y},
                 {"type": "pointerDown", "button": 0}]
        if kind == "swipe":
            x2, y2 = self._pts(a["x2"], a["y2"])
            steps.append({"type": "pause", "duration": 30})
            steps.append({"type": "pointerMove", "duration": max(ms, 80), "x": x2, "y": y2})
        else:
            steps.append({"type": "pause", "duration": max(ms, 40) if kind == "hold" else 40})
        steps.append({"type": "pointerUp", "button": 0})
        body = {"actions": [{"type": "pointer", "id": "finger1",
                             "parameters": {"pointerType": "touch"}, "actions": steps}]}
        r = self._req("POST", f"/session/{sid}/actions", body)
        err = (r.get("value") or {}).get("error") if isinstance(r.get("value"), dict) else None
        if err in ("invalid session id", "no such session"):
            self.session(fresh=True)
            r = self._req("POST", f"/session/{self.sid}/actions", body)
        return r


# ---------------------------------------------------------------------- engine
class Engine:
    def __init__(self, wda):
        self.wda = wda
        self.macro = []          # [{delay, kind, x, y, ...}]
        self.recording = False
        self.last_t = None
        self.stop_evt = threading.Event()
        self.worker = None
        self.progress = ""

    @property
    def playing(self):
        return self.worker is not None and self.worker.is_alive()

    def act(self, a):
        if self.recording:
            now = time.monotonic()
            # delay = time between the START of the previous gesture and this one
            a["delay"] = 0 if self.last_t is None else round(now - self.last_t, 3)
            self.last_t = now
            self.macro.append(a)
        self.wda.perform(a)

    def record(self, on):
        if on:
            self.stop()
            self.macro, self.last_t = [], None
        self.recording = on

    def stop(self):
        self.stop_evt.set()
        if self.worker:
            self.worker.join(timeout=5)
        self.stop_evt.clear()
        self.progress = ""

    def _run(self, fn):
        self.stop()
        self.recording = False
        self.worker = threading.Thread(target=fn, daemon=True)
        self.worker.start()

    def play(self, loops=1, speed=1.0, loop_gap=0.0):
        macro = list(self.macro)
        if not macro:
            raise RuntimeError("Nothing recorded")
        speed = max(float(speed), 0.01)

        def run():
            n = 0
            while not self.stop_evt.is_set() and (loops == 0 or n < loops):
                n += 1
                for i, a in enumerate(macro):
                    if self.stop_evt.wait(a.get("delay", 0) / speed):
                        return
                    self.progress = f"loop {n}{'' if loops == 0 else '/' + str(loops)} · step {i + 1}/{len(macro)}"
                    self.wda.perform(a)
                if loop_gap and self.stop_evt.wait(loop_gap):
                    return
            self.progress = "done"
        self._run(run)

    def autoclick(self, x, y, interval_ms=100, count=0):
        def run():
            i = 0
            while not self.stop_evt.is_set() and (count == 0 or i < count):
                i += 1
                self.progress = f"click {i}{'' if count == 0 else '/' + str(count)}"
                self.wda.perform({"kind": "tap", "x": x, "y": y})
                if self.stop_evt.wait(interval_ms / 1000):
                    return
            self.progress = "done"
        self._run(run)


# ---------------------------------------------------------------------- server
def make_handler(engine, mjpeg):
    page = (HERE / "ui.html").read_text(encoding="utf-8")

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json"):
            if not isinstance(body, (bytes, bytearray)):
                body = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self):
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}")

        def state(self):
            return {"recording": engine.recording, "playing": engine.playing,
                    "steps": len(engine.macro), "progress": engine.progress,
                    "size": engine.wda.size, "mjpeg": mjpeg}

        def do_GET(self):
            try:
                if self.path == "/":
                    return self._send(200, page.encode(), "text/html; charset=utf-8")
                if self.path.startswith("/api/screen"):
                    return self._send(200, engine.wda.screenshot_png(), "image/png")
                if self.path == "/api/state":
                    return self._send(200, self.state())
                if self.path == "/api/macro":
                    return self._send(200, {"version": 1, "steps": engine.macro})
                self._send(404, {"error": "not found"})
            except Exception as e:
                self._send(500, {"error": str(e)})

        def do_POST(self):
            try:
                b = self._json()
                p = self.path
                if p == "/api/action":
                    engine.act(b)
                elif p == "/api/record":
                    engine.record(bool(b.get("on")))
                elif p == "/api/play":
                    engine.play(int(b.get("loops", 1)), float(b.get("speed", 1)),
                                float(b.get("loopGap", 0)))
                elif p == "/api/autoclick":
                    engine.autoclick(float(b["x"]), float(b["y"]),
                                     float(b.get("interval", 100)), int(b.get("count", 0)))
                elif p == "/api/stop":
                    engine.stop()
                    engine.recording = False
                elif p == "/api/macro":
                    engine.stop()
                    engine.macro = list(b.get("steps", b if isinstance(b, list) else []))
                elif p == "/api/macro/delete":
                    i = int(b["index"])
                    if 0 <= i < len(engine.macro):
                        engine.macro.pop(i)
                else:
                    return self._send(404, {"error": "not found"})
                self._send(200, self.state())
            except Exception as e:
                self._send(500, {"error": str(e)})

    return H


# ------------------------------------------------------------------------- cli
def main():
    ap = argparse.ArgumentParser(description="TinyTask-style auto clicker for iPhone (via WebDriverAgent)")
    ap.add_argument("--wda", default="http://localhost:8100", help="WebDriverAgent URL")
    ap.add_argument("--mjpeg", default="http://localhost:9100",
                    help="WDA MJPEG stream URL for a smooth mirror ('' = poll screenshots)")
    ap.add_argument("--port", type=int, default=8765)
    sub = ap.add_subparsers(dest="cmd")
    pp = sub.add_parser("play", help="replay a saved macro without the UI")
    pp.add_argument("macro")
    pp.add_argument("--loops", type=int, default=1, help="0 = forever")
    pp.add_argument("--speed", type=float, default=1.0)
    pp.add_argument("--loop-gap", type=float, default=0.0, help="seconds between loops")
    args = ap.parse_args()

    wda = WDA(args.wda)
    try:
        wda.status()
        wda.session()
    except Exception as e:
        sys.exit(f"Can't reach WebDriverAgent at {args.wda}: {e}\nSee README.md (setup).")
    print(f"Connected to WDA, screen {wda.size[0]:.0f}x{wda.size[1]:.0f} pt")
    eng = Engine(wda)

    if args.cmd == "play":
        data = json.loads(Path(args.macro).read_text())
        eng.macro = data["steps"] if isinstance(data, dict) else data
        eng.play(args.loops, args.speed, args.loop_gap)
        try:
            while eng.playing:
                print("\r" + eng.progress.ljust(40), end="", flush=True)
                time.sleep(0.2)
        except KeyboardInterrupt:
            eng.stop()
        print("\nstopped" if eng.progress != "done" else "\ndone")
        return

    srv = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(eng, args.mjpeg))
    print(f"Open http://localhost:{args.port}   (Ctrl+C to quit)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        eng.stop()


if __name__ == "__main__":
    main()
