import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GUI = ROOT / "gui.py"
BACKEND = ROOT / "device_backend.py"
BOT = ROOT / "bot.py"


class TugarinBotsGuiSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gui = GUI.read_text(encoding="utf-8-sig")
        cls.backend = BACKEND.read_text(encoding="utf-8-sig")
        cls.bot = BOT.read_text(encoding="utf-8-sig")

    def test_fresh_install_handles_only_explicit_notification_permission(self):
        self.assertIn("handle_known_notification_permission()", self.bot)
        self.assertIn("com.android.permissioncontroller:id/permission_allow_button", self.bot)
        self.assertIn("permission_message", self.bot)
        self.assertIn("def ui_click_resource", self.backend)

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

    def test_gui_uses_continuous_stream_and_complete_manual_controls(self):
        self.assertIn("ContinuousFrameStream", self.gui)
        self.assertIn("hold_requested = Signal(int, int, int)", self.gui)
        self.assertIn("def wheelEvent", self.gui)
        self.assertIn("KEYCODE_BACK", self.gui)
        self.assertIn("volume_mute", self.gui)
        self.assertIn("stream_metrics_ready", self.gui)

    def test_operator_recovery_is_visible_and_backend_debug_is_hidden_by_default(self):
        self.assertIn("БЫСТРОЕ ВОССТАНОВЛЕНИЕ", self.gui)
        self.assertIn("def restart_game", self.gui)
        self.assertIn("Режим разработчика", self.gui)
        self.assertIn("card.setVisible(False)", self.gui)

    def test_gui_children_prefer_consoleless_python(self):
        self.assertIn("def consoleless_python(path):", self.gui)
        self.assertIn('"pythonw.exe"', self.gui)
        self.assertNotIn("self.process.start(self.python_path.text().strip()", self.gui)

    def test_gui_does_not_start_a_second_video_pipeline_while_bot_runs(self):
        self.assertIn("bot-shared-jpeg", self.gui)
        self.assertIn("LIVE_FRAME_FILE", (ROOT / "bot.py").read_text(encoding="utf-8-sig"))
        self.assertIn("if bot_pid() is not None", self.gui)
        self.assertIn("self._stop_frame_stream()", self.gui)

    def test_gui_maps_cropped_viewport_back_to_framebuffer_coordinates(self):
        self.assertIn("def _crop_for_render(frame, rect):", self.gui)
        self.assertIn("rect.get(\"left\", 0)", self.gui)
        self.assertIn("self._device_left + round", self.gui)

    def test_wsa_prefers_exact_visible_game_window_capture(self):
        bot_source = (ROOT / "bot.py").read_text(encoding="utf-8-sig")
        self.assertIn("class WsaGameWindowCapture", bot_source)
        self.assertIn("GetClientRect", bot_source)
        self.assertIn('transport_name = "wsa-window"', bot_source)
        self.assertIn("bot.WsaGameWindowCapture()", self.gui)


if __name__ == "__main__":
    unittest.main()
