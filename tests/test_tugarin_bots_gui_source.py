import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GUI = ROOT / "gui.py"
BACKEND = ROOT / "device_backend.py"


class TugarinBotsGuiSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gui = GUI.read_text(encoding="utf-8-sig")
        cls.backend = BACKEND.read_text(encoding="utf-8-sig")

    def test_product_brand_is_visible_in_gui(self):
        self.assertIn("TUGARIN BOTS — Центр управления", self.gui)
        self.assertIn('app.setApplicationName("TUGARIN BOTS")', self.gui)

    def test_embedded_preview_supports_manual_pointer_and_keyboard_input(self):
        self.assertIn("class InteractivePreview(QLabel)", self.gui)
        self.assertIn("tap_requested = Signal(int, int)", self.gui)
        self.assertIn("swipe_requested = Signal(int, int, int, int, int)", self.gui)
        self.assertIn("def keyPressEvent", self.gui)
        self.assertIn("def _pause_for_manual_control", self.gui)

    def test_runtime_health_includes_network_and_audio(self):
        self.assertIn("network_ready: bool = False", self.backend)
        self.assertIn("internet_reachable: bool = False", self.backend)
        self.assertIn("audio_service_ready: bool = False", self.backend)
        self.assertIn('self.shell(["dumpsys", "audio"]', self.backend)

    def test_gui_is_single_instance_and_reports_runtime_identity(self):
        self.assertIn("QLockFile", self.gui)
        self.assertIn("tugarin-bots-gui.lock", self.gui)
        self.assertIn("Windows SID/User", self.gui)
        self.assertIn("WSA flavor", self.gui)
        self.assertIn("ADB authorization", self.gui)
        self.assertIn("Google Services", self.gui)
        self.assertIn("P0 manifest", self.gui)

    def test_gui_children_prefer_consoleless_python(self):
        self.assertIn("def consoleless_python(path):", self.gui)
        self.assertIn('"pythonw.exe"', self.gui)
        self.assertNotIn("self.process.start(self.python_path.text().strip()", self.gui)


if __name__ == "__main__":
    unittest.main()
