#!/usr/bin/env python3
"""SwiftDeploy API Service — stable or canary mode."""

import os
import time
import random
import threading
import json
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler

# ── Config ────────────────────────────────────────────────────────────────────
MODE        = os.environ.get("MODE", "stable").lower()
APP_VERSION = os.environ.get("APP_VERSION", "1.0.0")
APP_PORT    = int(os.environ.get("APP_PORT", "3000"))
START_TIME  = time.time()

# ── Chaos state ───────────────────────────────────────────────────────────────
_lock  = threading.Lock()
_chaos: dict = {"mode": None, "duration": None, "rate": None, "timer": None}


def _reset_chaos():
    with _lock:
        if _chaos["timer"]:
            _chaos["timer"].cancel()
        _chaos.update(mode=None, duration=None, rate=None, timer=None)


def _apply_chaos():
    """Returns (should_500: bool, sleep_secs: float)."""
    with _lock:
        m = _chaos["mode"]
        if m == "slow":
            return False, float(_chaos["duration"] or 5)
        if m == "error":
            return random.random() < float(_chaos["rate"] or 0.5), 0.0
    return False, 0.0


# ── Request handler ───────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):

    def log_message(self, *_):  # suppress default stderr noise
        pass

    def _json(self, code: int, body: dict, extra: dict | None = None):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("X-Deployed-By", "swiftdeploy")
        if MODE == "canary":
            self.send_header("X-Mode", "canary")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(raw)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length", 0))
        if n:
            try:
                return json.loads(self.rfile.read(n))
            except json.JSONDecodeError:
                return {}
        return {}

    # GET /
    def _root(self):
        err, sleep = _apply_chaos()
        if sleep:
            time.sleep(sleep)
        if err:
            self._json(500, {"error": "chaos-induced failure", "mode": MODE})
            return
        self._json(200, {
            "message": f"Welcome to SwiftDeploy — running in {MODE} mode",
            "mode":    MODE,
            "version": APP_VERSION,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    # GET /healthz
    def _healthz(self):
        self._json(200, {
            "status":         "ok",
            "mode":           MODE,
            "uptime_seconds": round(time.time() - START_TIME, 2),
            "version":        APP_VERSION,
        })

    # POST /chaos
    def _chaos(self):
        if MODE != "canary":
            self._json(403, {"error": "chaos endpoint only available in canary mode"})
            return

        body = self._body()
        m    = body.get("mode")

        if m == "recover":
            _reset_chaos()
            self._json(200, {"status": "recovered", "chaos": None})

        elif m == "slow":
            dur = int(body.get("duration", 5))
            _reset_chaos()
            with _lock:
                _chaos.update(mode="slow", duration=dur)
                t = threading.Timer(300, _reset_chaos)
                t.daemon = True
                _chaos["timer"] = t
                t.start()
            self._json(200, {"status": "chaos active", "mode": "slow", "duration": dur})

        elif m == "error":
            rate = float(body.get("rate", 0.5))
            _reset_chaos()
            with _lock:
                _chaos.update(mode="error", rate=rate)
            self._json(200, {"status": "chaos active", "mode": "error", "rate": rate})

        else:
            self._json(400, {"error": "invalid mode", "valid": ["slow", "error", "recover"]})

    # Router
    def do_GET(self):
        if   self.path == "/":       self._root()
        elif self.path == "/healthz": self._healthz()
        else: self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/chaos": self._chaos()
        else: self._json(404, {"error": "not found"})


# ── Entry ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", APP_PORT), Handler)
    print(f"[swiftdeploy] mode={MODE} version={APP_VERSION} port={APP_PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("[swiftdeploy] shutdown", flush=True)
