"""
Unit tests for swiftdeploy CLI helper functions.
These run without Docker / network by patching subprocess calls.
"""

import os
import sys
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# ── Locate project root ───────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

# Import swiftdeploy module (it's a script with no .py extension — load via source)
import importlib.util
from importlib.machinery import SourceFileLoader

_loader = SourceFileLoader("swiftdeploy_cli", str(PROJECT_ROOT / "swiftdeploy"))
_spec   = importlib.util.spec_from_loader("swiftdeploy_cli", _loader)
_mod    = importlib.util.module_from_spec(_spec)
_loader.exec_module(_mod)
sd = _mod   # alias


class TestManifestContext(unittest.TestCase):
    """manifest_context() must extract all template fields correctly."""

    MANIFEST = {
        "services": {
            "image": "swift-deploy-1-node:latest",
            "port":  3000,
            "mode":  "stable",
            "version": "1.2.3",
            "restart_policy": "unless-stopped",
            "log_volume": "swiftdeploy-logs",
        },
        "nginx": {
            "image": "nginx:latest",
            "port":  8080,
            "proxy_timeout": 30,
        },
        "network": {
            "name": "swiftdeploy-net",
            "driver_type": "bridge",
        },
    }

    def test_service_image(self):
        ctx = sd.manifest_context(self.MANIFEST)
        self.assertEqual(ctx["service_image"], "swift-deploy-1-node:latest")

    def test_service_port(self):
        ctx = sd.manifest_context(self.MANIFEST)
        self.assertEqual(ctx["service_port"], 3000)

    def test_nginx_port(self):
        ctx = sd.manifest_context(self.MANIFEST)
        self.assertEqual(ctx["nginx_port"], 8080)

    def test_network_name(self):
        ctx = sd.manifest_context(self.MANIFEST)
        self.assertEqual(ctx["network_name"], "swiftdeploy-net")

    def test_mode(self):
        ctx = sd.manifest_context(self.MANIFEST)
        self.assertEqual(ctx["mode"], "stable")

    def test_proxy_timeout(self):
        ctx = sd.manifest_context(self.MANIFEST)
        self.assertEqual(ctx["proxy_timeout"], 30)

    def test_log_volume(self):
        ctx = sd.manifest_context(self.MANIFEST)
        self.assertEqual(ctx["log_volume"], "swiftdeploy-logs")


class TestPortInUse(unittest.TestCase):
    """port_in_use() should detect when a port is occupied."""

    def test_very_high_port_is_free(self):
        # Port 59999 is almost certainly free
        self.assertFalse(sd.port_in_use(59999))

    def test_port_in_use_returns_bool(self):
        result = sd.port_in_use(59998)
        self.assertIsInstance(result, bool)


class TestDockerImageExists(unittest.TestCase):

    @patch("subprocess.run")
    def test_image_found(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        self.assertTrue(sd.docker_image_exists("nginx:latest"))

    @patch("subprocess.run")
    def test_image_not_found(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        self.assertFalse(sd.docker_image_exists("no-such-image:xyz"))


class TestTemplateRendering(unittest.TestCase):
    """render_template() must produce non-empty output with all placeholders filled."""

    CTX = {
        "service_image":  "swift-deploy-1-node:latest",
        "service_port":   3000,
        "service_name":   "app",
        "mode":           "stable",
        "version":        "1.0.0",
        "restart_policy": "unless-stopped",
        "log_volume":     "swiftdeploy-logs",
        "nginx_image":    "nginx:latest",
        "nginx_port":     8080,
        "proxy_timeout":  30,
        "network_name":   "swiftdeploy-net",
        "network_driver": "bridge",
    }

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _render(self, template_name: str) -> str:
        out = Path(self.tmp) / template_name.replace(".j2", "")
        # Temporarily override TEMPLATES_DIR
        orig = sd.TEMPLATES_DIR
        sd.TEMPLATES_DIR = PROJECT_ROOT / "templates"
        sd.render_template(template_name, self.CTX, out)
        sd.TEMPLATES_DIR = orig
        return out.read_text()

    def test_nginx_conf_generated(self):
        text = self._render("nginx.conf.j2")
        self.assertIn("listen 8080", text)
        self.assertIn("server app:3000", text)
        self.assertIn("X-Deployed-By", text)
        self.assertIn("swiftdeploy_fmt", text)

    def test_nginx_conf_has_error_pages(self):
        text = self._render("nginx.conf.j2")
        self.assertIn("502", text)
        self.assertIn("503", text)
        self.assertIn("504", text)

    def test_nginx_conf_has_proxy_timeout(self):
        text = self._render("nginx.conf.j2")
        self.assertIn("proxy_read_timeout", text)
        self.assertIn("30s", text)

    def test_docker_compose_generated(self):
        text = self._render("docker-compose.yml.j2")
        self.assertIn("swift-deploy-1-node:latest", text)
        self.assertIn("MODE", text)
        self.assertIn("swiftdeploy-net", text)
        self.assertIn("swiftdeploy-logs", text)

    def test_docker_compose_no_direct_port_expose(self):
        text = self._render("docker-compose.yml.j2")
        # Service port must NOT be published to host
        self.assertNotIn('"3000:3000"', text)
        self.assertNotIn("'3000:3000'", text)

    def test_docker_compose_has_cap_drop(self):
        text = self._render("docker-compose.yml.j2")
        self.assertIn("cap_drop", text)
        self.assertIn("ALL", text)

    def test_docker_compose_has_healthcheck(self):
        text = self._render("docker-compose.yml.j2")
        self.assertIn("healthcheck", text)
        self.assertIn("/healthz", text)

    def test_docker_compose_mode_injected(self):
        text = self._render("docker-compose.yml.j2")
        self.assertIn("MODE", text)
        self.assertIn("stable", text)


class TestManifestLoad(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._orig_path = sd.MANIFEST_PATH

    def tearDown(self):
        sd.MANIFEST_PATH = self._orig_path
        shutil.rmtree(self.tmp)

    def _write_manifest(self, content: str) -> Path:
        p = Path(self.tmp) / "manifest.yaml"
        p.write_text(content)
        sd.MANIFEST_PATH = p
        return p

    def test_valid_manifest_loads(self):
        self._write_manifest("""
services:
  image: swift-deploy-1-node:latest
  port: 3000
nginx:
  image: nginx:latest
  port: 8080
network:
  name: swiftdeploy-net
  driver_type: bridge
""")
        m = sd.load_manifest()
        self.assertEqual(m["services"]["image"], "swift-deploy-1-node:latest")
        self.assertEqual(m["nginx"]["port"], 8080)

    def test_save_and_reload_manifest(self):
        self._write_manifest("""
services:
  image: swift-deploy-1-node:latest
  port: 3000
  mode: stable
nginx:
  image: nginx:latest
  port: 8080
network:
  name: swiftdeploy-net
  driver_type: bridge
""")
        m = sd.load_manifest()
        m["services"]["mode"] = "canary"
        sd.save_manifest(m)
        m2 = sd.load_manifest()
        self.assertEqual(m2["services"]["mode"], "canary")


if __name__ == "__main__":
    unittest.main()
