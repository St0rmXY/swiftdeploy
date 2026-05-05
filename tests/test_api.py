"""
Test suite for the SwiftDeploy API service.
Runs against a live server on TEST_PORT (default 3001).
Can also be run with MOCK=1 to stub the server for CI without Docker.
"""

import os
import json
import time
import threading
import unittest
import urllib.request
import urllib.error
from http.server import HTTPServer

# Import the actual handler so we can spin a real instance in-process
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

# Override env before import
TEST_PORT    = int(os.environ.get("TEST_PORT", "3001"))
os.environ.setdefault("APP_PORT",    str(TEST_PORT))
os.environ.setdefault("APP_VERSION", "test-1.0.0")

import main as app_module   # noqa: E402  (after env setup)


# ── In-process test server ────────────────────────────────────────────────────

_server: HTTPServer | None = None
_thread: threading.Thread | None = None


def setUpModule():
    global _server, _thread
    # Reset chaos state before all tests
    app_module._reset_chaos()

    _server = HTTPServer(("127.0.0.1", TEST_PORT), app_module.Handler)
    _thread = threading.Thread(target=_server.serve_forever, daemon=True)
    _thread.start()
    # Wait until port is accepting connections
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{TEST_PORT}/healthz", timeout=1)
            break
        except Exception:
            time.sleep(0.1)


def tearDownModule():
    if _server:
        _server.shutdown()


def _get(path) -> tuple[int, dict]:
    url = f"http://127.0.0.1:{TEST_PORT}{path}"
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _post(path, body: dict) -> tuple[int, dict]:
    url  = f"http://127.0.0.1:{TEST_PORT}{path}"
    data = json.dumps(body).encode()
    req  = urllib.request.Request(url, data=data, method="POST",
                                  headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _headers(path) -> dict:
    url = f"http://127.0.0.1:{TEST_PORT}{path}"
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return dict(r.headers)
    except urllib.error.HTTPError as e:
        return dict(e.headers)


# ── Test cases ────────────────────────────────────────────────────────────────

class TestRoot(unittest.TestCase):

    def setUp(self):
        app_module._reset_chaos()

    def test_root_returns_200(self):
        code, body = _get("/")
        self.assertEqual(code, 200)

    def test_root_has_message(self):
        _, body = _get("/")
        self.assertIn("message", body)

    def test_root_has_mode(self):
        _, body = _get("/")
        self.assertIn("mode", body)
        self.assertIn(body["mode"], ("stable", "canary"))

    def test_root_has_version(self):
        _, body = _get("/")
        self.assertIn("version", body)
        self.assertEqual(body["version"], "test-1.0.0")

    def test_root_has_timestamp(self):
        _, body = _get("/")
        self.assertIn("timestamp", body)
        # ISO 8601 format check
        self.assertRegex(body["timestamp"], r"\d{4}-\d{2}-\d{2}T")

    def test_root_has_deployed_by_header(self):
        h = _headers("/")
        self.assertEqual(h.get("X-Deployed-By"), "swiftdeploy")

    def test_unknown_path_returns_404(self):
        code, _ = _get("/nonexistent")
        self.assertEqual(code, 404)


class TestHealthz(unittest.TestCase):

    def test_healthz_returns_200(self):
        code, _ = _get("/healthz")
        self.assertEqual(code, 200)

    def test_healthz_status_ok(self):
        _, body = _get("/healthz")
        self.assertEqual(body.get("status"), "ok")

    def test_healthz_has_uptime(self):
        _, body = _get("/healthz")
        self.assertIn("uptime_seconds", body)
        self.assertGreaterEqual(body["uptime_seconds"], 0)

    def test_healthz_has_mode(self):
        _, body = _get("/healthz")
        self.assertIn("mode", body)

    def test_healthz_has_version(self):
        _, body = _get("/healthz")
        self.assertIn("version", body)


class TestChaosEndpointStableMode(unittest.TestCase):
    """Chaos must be blocked in stable mode."""

    def setUp(self):
        app_module._reset_chaos()
        # Force stable mode for these tests
        self._original_mode = app_module.MODE
        app_module.MODE = "stable"

    def tearDown(self):
        app_module.MODE = self._original_mode
        app_module._reset_chaos()

    def test_chaos_blocked_in_stable(self):
        code, body = _post("/chaos", {"mode": "recover"})
        self.assertEqual(code, 403)
        self.assertIn("error", body)


class TestChaosEndpointCanaryMode(unittest.TestCase):
    """Chaos must work in canary mode."""

    def setUp(self):
        app_module._reset_chaos()
        self._original_mode = app_module.MODE
        app_module.MODE = "canary"

    def tearDown(self):
        app_module.MODE = self._original_mode
        app_module._reset_chaos()

    def test_chaos_recover_returns_200(self):
        code, body = _post("/chaos", {"mode": "recover"})
        self.assertEqual(code, 200)
        self.assertEqual(body.get("status"), "recovered")

    def test_chaos_error_mode_activates(self):
        code, body = _post("/chaos", {"mode": "error", "rate": 1.0})
        self.assertEqual(code, 200)
        self.assertEqual(body.get("mode"), "error")
        self.assertEqual(body.get("rate"), 1.0)

    def test_chaos_error_rate_1_causes_500(self):
        # Rate 1.0 → every request fails
        _post("/chaos", {"mode": "error", "rate": 1.0})
        code, _ = _get("/")
        self.assertEqual(code, 500)

    def test_chaos_recover_clears_errors(self):
        _post("/chaos", {"mode": "error", "rate": 1.0})
        _post("/chaos", {"mode": "recover"})
        code, _ = _get("/")
        self.assertEqual(code, 200)

    def test_chaos_slow_mode_activates(self):
        code, body = _post("/chaos", {"mode": "slow", "duration": 1})
        self.assertEqual(code, 200)
        self.assertEqual(body.get("mode"), "slow")

    def test_chaos_invalid_mode_returns_400(self):
        code, body = _post("/chaos", {"mode": "explode"})
        self.assertEqual(code, 400)
        self.assertIn("valid", body)

    def test_canary_mode_sets_x_mode_header(self):
        h = _headers("/")
        self.assertEqual(h.get("X-Mode"), "canary")


class TestHeaders(unittest.TestCase):

    def test_content_type_json(self):
        h = _headers("/healthz")
        ct = h.get("Content-Type", "")
        self.assertIn("application/json", ct)

    def test_deployed_by_header_on_healthz(self):
        h = _headers("/healthz")
        self.assertEqual(h.get("X-Deployed-By"), "swiftdeploy")


if __name__ == "__main__":
    unittest.main()
