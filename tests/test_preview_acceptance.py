import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "scripts" / "verify_preview.ps1").read_text(encoding="utf-8-sig")


class PreviewAcceptanceScriptTests(unittest.TestCase):
    def test_preview_gate_requires_production_fast_transport_by_default(self):
        self.assertIn("preview-smoke", SOURCE)
        self.assertIn("--require-fast", SOURCE)
        self.assertIn("wsa-window or proven scrcpy H.264", SOURCE)
        self.assertIn('"15"', SOURCE)
        self.assertIn("PREVIEW HOST GATE PASS", SOURCE)
        self.assertIn("PREVIEW HOST GATE FAIL", SOURCE)

    def test_preview_gate_runs_probe_after_failure(self):
        self.assertIn("preview-probe", SOURCE)
        self.assertIn("preview-h264-probe.json", SOURCE)
        self.assertIn("Collecting optional scrcpy H.264 transport diagnostics", SOURCE)

    def test_preview_gate_provisions_pinned_scrcpy_server(self):
        self.assertIn("provision_scrcpy_server.ps1", SOURCE)
        self.assertIn("scrcpy-server provisioning failed", SOURCE)
        self.assertIn("provision_scrcpy_server.ps1", SOURCE)

    def test_preview_gate_uses_tugarin_venv(self):
        self.assertIn(r"C:\warbot_wsa\tugarin-venv\Scripts\python.exe", SOURCE)
        self.assertIn(r"C:\warbot_wsa\release-venv\Scripts\python.exe", SOURCE)


if __name__ == "__main__":
    unittest.main()
