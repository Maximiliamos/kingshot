import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "scripts" / "verify_gui.ps1").read_text(encoding="utf-8-sig")
GUI = (ROOT / "gui.py").read_text(encoding="utf-8")


class GuiHostAcceptanceTests(unittest.TestCase):
    def test_gui_smoke_uses_consoleless_pythonw_and_self_closes(self):
        self.assertIn("pythonw.exe", SOURCE)
        self.assertIn("TUGARIN_GUI_HOST_SMOKE_SECONDS", SOURCE)
        self.assertIn("GUI HOST GATE PASS", SOURCE)
        self.assertIn("GUI HOST GATE FAIL", SOURCE)
        self.assertIn("finish_host_smoke", GUI)
        self.assertIn("app.exit(0 if report", GUI)

    def test_gui_smoke_requires_rendered_scrcpy_frame(self):
        self.assertIn('"has_rendered_frame"', GUI)
        self.assertIn('"scrcpy-h264" in stream_text', GUI)
        self.assertIn("gui-host-smoke.json", SOURCE)


if __name__ == "__main__":
    unittest.main()
