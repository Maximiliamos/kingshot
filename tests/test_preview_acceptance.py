import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "scripts" / "verify_preview.ps1").read_text(encoding="utf-8-sig")


class PreviewAcceptanceScriptTests(unittest.TestCase):
    def test_preview_gate_requires_h264_by_default(self):
        self.assertIn("preview-smoke", SOURCE)
        self.assertIn("--require-h264", SOURCE)
        self.assertIn("PREVIEW HOST GATE PASS", SOURCE)
        self.assertIn("PREVIEW HOST GATE FAIL", SOURCE)

    def test_preview_gate_uses_tugarin_venv(self):
        self.assertIn(r"C:\warbot_wsa\tugarin-venv\Scripts\python.exe", SOURCE)
        self.assertIn(r"C:\warbot_wsa\release-venv\Scripts\python.exe", SOURCE)


if __name__ == "__main__":
    unittest.main()
