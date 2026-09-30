import io
import json
import os
import unittest
from tempfile import TemporaryDirectory
from pathlib import Path

import numpy as np
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import warbot_cli
from device_backend import BackendError, DeviceHealth


class WarBotCliTests(unittest.TestCase):
    def test_status_prints_backend_health(self):
        backend = MagicMock()
        backend.health.return_value = DeviceHealth(
            backend="adb",
            serial="device-1",
            state="device",
            boot_completed="1",
            abi="arm64-v8a",
            abilist="arm64-v8a",
        )
        out = io.StringIO()
        with patch("warbot_cli.create_backend", return_value=backend), redirect_stdout(out):
            code = warbot_cli.main(["status", "--backend", "adb", "--serial", "device-1"])
        self.assertEqual(code, 0)
        self.assertIn('"state": "device"', out.getvalue())
        self.assertIn('"ready": true', out.getvalue())

    def test_preview_smoke_reports_transport_metrics(self):
        backend = MagicMock()
        backend.require_ready.return_value = DeviceHealth(
            backend="wsa",
            serial="127.0.0.1:58526",
            state="device",
            boot_completed="1",
        )

        class FakeCapture:
            transport_name = "h264-screenrecord"

            def __init__(self):
                self.closed = False

            def grab(self):
                return (
                    np.zeros((20, 10, 3), dtype=np.uint8),
                    "fake",
                    {"left": 0, "top": 0, "width": 10, "height": 20},
                )

            def close(self):
                self.closed = True

        capture = FakeCapture()
        out = io.StringIO()
        with patch("warbot_cli.create_backend", return_value=backend), \
                patch("warbot_cli.create_preview_capture", return_value=capture), \
                patch("warbot_cli.time.monotonic", side_effect=[0.0, 0.0, 0.01, 0.5, 0.5, 0.51, 1.0, 1.0, 1.01, 1.5, 1.5, 1.51, 2.1, 2.1, 2.1]), \
                redirect_stdout(out):
            code = warbot_cli.main([
                "preview-smoke", "--backend", "wsa",
                "--preview-seconds", "2", "--require-h264",
            ])

        self.assertEqual(code, 0)
        self.assertTrue(capture.closed)
        payload = json.loads(out.getvalue())
        self.assertTrue(payload["pass"])
        self.assertTrue(payload["h264"])
        self.assertEqual(payload["active_transport"], "h264-screenrecord")
        self.assertGreaterEqual(payload["frames"], 3)

    def test_preview_probe_uses_direct_scrcpy_transport(self):
        backend = MagicMock()
        backend.require_ready.return_value = DeviceHealth(
            backend="wsa",
            serial="127.0.0.1:58526",
            state="device",
            boot_completed="1",
            android="13",
            resolution="Physical size: 1920x1080",
        )

        class FakeScrcpy:
            def __init__(self, *args, **kwargs):
                self.closed = False

            def grab(self):
                return (
                    np.zeros((720, 1280, 3), dtype=np.uint8),
                    "scrcpy-h264",
                    {"left": 0, "top": 0, "width": 1920, "height": 1080},
                )

            def diagnostics(self):
                return {"transport": "scrcpy-h264", "server_exit": None}

            def close(self):
                self.closed = True

        fake_server = MagicMock()
        fake_server.is_file.return_value = True
        out = io.StringIO()
        with TemporaryDirectory() as td, \
                patch("warbot_cli.ScrcpyServerCapture", FakeScrcpy), \
                patch("warbot_cli.default_server_path", return_value=fake_server), \
                patch("warbot_cli.time.monotonic", side_effect=[
                    0.0, 0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6,
                    0.7, 0.8, 0.9, 1.0, 1.1, 1.2
                ]), \
                redirect_stdout(out):
            old_cwd = os.getcwd()
            os.chdir(td)
            try:
                report = warbot_cli.probe_h264_transport(backend)
            finally:
                os.chdir(old_cwd)

        self.assertTrue(report["pass"])
        self.assertGreaterEqual(report["frames"], 3)
        self.assertEqual(report["diagnostics"]["transport"], "scrcpy-h264")
        self.assertIn("preview-h264-probe.json", report["report_path"])

    def test_clear_game_data_requires_explicit_yes(self):
        backend = MagicMock()
        with patch("warbot_cli.create_backend", return_value=backend):
            with self.assertRaises(BackendError):
                warbot_cli.main(["clear-game-data", "--backend", "adb"])
        backend.clear_app_data.assert_not_called()

    def test_loading_logo_gate_is_independent_from_full_bot_runtime(self):
        import inspect
        source = inspect.getsource(warbot_cli.loading_logo_visible)
        self.assertNotIn("import bot", source)
        self.assertIn("templates", source)
        self.assertIn("loading_logo.png", source)
        self.assertIn("cv2.matchTemplate", source)

    def test_bootstrap_prepares_native_runtime_and_game(self):
        backend = warbot_cli.NativeArm64Backend(
            serial="device-1",
            adb_path=r"C:\\fake\\adb.exe",
        )
        ready = DeviceHealth(
            backend="native_arm64",
            serial="device-1",
            state="device",
            boot_completed="1",
            android="11",
            abi="arm64-v8a",
            abilist="arm64-v8a",
            native_bridge="",
        )
        with TemporaryDirectory() as td:
            output = str(Path(td) / "frame.png")
            with patch("warbot_cli.create_backend", return_value=backend), \
                    patch.object(backend, "health", return_value=ready), \
                    patch.object(backend, "require_ready", return_value=ready), \
                    patch.object(backend, "wait_runtime_services", return_value=ready) as services, \
                    patch.object(backend, "package_installed", return_value=False), \
                    patch.object(backend, "install_verified_game", return_value="Success") as install, \
                    patch.object(backend, "launch_app", return_value="Starting"), \
                    patch.object(backend, "wait_package_running", return_value="1234"), \
                    patch.object(backend, "wait_package_stable", return_value="1234") as stable, \
                    patch.object(backend, "frame", return_value=np.zeros((20, 10, 3), dtype=np.uint8)), \
                    patch("warbot_cli.loading_logo_visible", return_value=False):
                out = io.StringIO()
                with redirect_stdout(out):
                    code = warbot_cli.main(["bootstrap", "--output", output])

            self.assertEqual(code, 0)
            services.assert_called_once_with(timeout=90)
            install.assert_called_once()
            stable.assert_called_once_with(45, expected_pid="1234")
            self.assertTrue(Path(output).is_file())
            self.assertTrue(Path(output).with_name("frame-startup.png").is_file())
            self.assertIn('"native_arm64": true', out.getvalue())

    def test_bootstrap_supports_wsa_translation_backend(self):
        backend = warbot_cli.WsaBackend(
            serial="127.0.0.1:58526",
            adb_path=r"C:\\fake\\adb.exe",
        )
        ready = DeviceHealth(
            backend="wsa",
            serial="127.0.0.1:58526",
            state="device",
            boot_completed="1",
            android="13",
            abi="x86_64",
            abilist="x86_64,x86,arm64-v8a,armeabi-v7a",
            native_bridge="libhoudini.so",
        )
        with TemporaryDirectory() as td:
            output = str(Path(td) / "wsa-frame.png")
            with patch("warbot_cli.create_backend", return_value=backend), \
                    patch.object(backend, "health", return_value=ready), \
                    patch.object(backend, "require_ready", return_value=ready) as require_ready, \
                    patch.object(backend, "wait_runtime_services", return_value=ready) as services, \
                    patch.object(backend, "package_installed", return_value=False), \
                    patch.object(backend, "install_verified_game", return_value="Success") as install, \
                    patch.object(backend, "launch_app", return_value="Starting"), \
                    patch.object(backend, "wait_package_running", return_value="5678"), \
                    patch.object(backend, "wait_package_stable", return_value="5678"), \
                    patch.object(backend, "frame", return_value=np.zeros((20, 10, 3), dtype=np.uint8)), \
                    patch("warbot_cli.loading_logo_visible", return_value=False):
                out = io.StringIO()
                with redirect_stdout(out):
                    code = warbot_cli.main([
                        "bootstrap", "--backend", "wsa",
                        "--serial", "127.0.0.1:58526",
                        "--output", output,
                    ])

            self.assertEqual(code, 0)
            require_ready.assert_called_once_with(native_arm64=False)
            services.assert_called_once_with(timeout=90)
            install.assert_called_once()
            self.assertTrue(Path(output).is_file())
            self.assertTrue(Path(output).with_name("wsa-frame-startup.png").is_file())
            self.assertIn('"backend": "wsa"', out.getvalue())
            self.assertIn('"native_bridge": "libhoudini.so"', out.getvalue())

    def test_bootstrap_fails_if_game_remains_on_loading_screen(self):
        backend = warbot_cli.WsaBackend(
            serial="127.0.0.1:58526",
            adb_path=r"C:\\fake\\adb.exe",
        )
        ready = DeviceHealth(
            backend="wsa",
            serial="127.0.0.1:58526",
            state="device",
            boot_completed="1",
            android="13",
            abi="x86_64",
            network_ready=True,
            internet_reachable=True,
            audio_service_ready=True,
        )
        with TemporaryDirectory() as td:
            output = str(Path(td) / "stuck.png")
            with patch("warbot_cli.create_backend", return_value=backend), \
                    patch.object(backend, "health", return_value=ready), \
                    patch.object(backend, "require_ready", return_value=ready), \
                    patch.object(backend, "wait_runtime_services", return_value=ready), \
                    patch.object(backend, "package_installed", return_value=True), \
                    patch.object(backend, "launch_app", return_value="Starting"), \
                    patch.object(backend, "wait_package_running", return_value="777"), \
                    patch.object(backend, "wait_package_stable", return_value="777"), \
                    patch.object(backend, "frame", return_value=np.zeros((20, 10, 3), dtype=np.uint8)), \
                    patch.object(backend, "collect_game_crash", return_value=Path(td) / "crash.txt"), \
                    patch("warbot_cli.loading_logo_visible", side_effect=[True, True]):
                with self.assertRaises(BackendError) as ctx:
                    warbot_cli.main([
                        "bootstrap", "--backend", "wsa",
                        "--serial", "127.0.0.1:58526",
                        "--game-stability-seconds", "1",
                        "--output", output,
                    ])
        self.assertIn("remained on the verified loading screen", str(ctx.exception))

    def test_clean_start_preserves_pc_nickname_counter(self):
        backend = warbot_cli.NativeArm64Backend(
            serial="device-1",
            adb_path=r"C:\\fake\\adb.exe",
        )
        ready = DeviceHealth(
            backend="native_arm64",
            serial="device-1",
            state="device",
            boot_completed="1",
            abi="arm64-v8a",
            abilist="arm64-v8a",
            native_bridge="",
        )
        old = {
            "next_nickname": 14,
            "characters_created": 13,
            "current_cycle": 4,
            "characters_per_cycle": 4,
            "auto_reset_data": True,
            "repeat_cycles": True,
            "target_state": 3,
        }
        with patch("warbot_cli.create_backend", return_value=backend), \
                patch.object(backend, "require_ready", return_value=ready), \
                patch.object(backend, "package_installed", return_value=True), \
                patch.object(backend, "stop_app"), \
                patch.object(backend, "clear_app_data", return_value="Success"), \
                patch.object(backend, "launch_app"), \
                patch("bot.load_state", return_value=old), \
                patch("bot.save_state") as save_state:
            code = warbot_cli.main(["clean-start", "--yes"])

        self.assertEqual(code, 0)
        saved = save_state.call_args.args[0]
        self.assertEqual(saved["next_nickname"], 14)
        self.assertEqual(saved["characters_created"], 13)
        self.assertEqual(saved["phase"], "tutorial_new_character")
        self.assertEqual(saved["tutorial_origin"], "initial")

    def test_restart_game_is_bounded_to_app_lifecycle(self):
        backend = MagicMock()
        backend.require_ready.return_value = DeviceHealth(
            backend="wsa",
            serial="127.0.0.1:58526",
            state="device",
            boot_completed="1",
        )
        backend.wait_package_running.return_value = "4242"
        out = io.StringIO()
        with patch("warbot_cli.create_backend", return_value=backend), redirect_stdout(out):
            code = warbot_cli.main(["restart-game", "--backend", "wsa"])

        self.assertEqual(code, 0)
        backend.stop_app.assert_called_once()
        backend.launch_app.assert_called_once()
        backend.wait_package_running.assert_called_once_with(timeout=90)
        self.assertIn('"game_pid": "4242"', out.getvalue())

    def test_tap_routes_coordinates_to_backend(self):
        backend = MagicMock()
        with patch("warbot_cli.create_backend", return_value=backend):
            code = warbot_cli.main(["tap", "--backend", "adb", "120", "340"])
        self.assertEqual(code, 0)
        backend.tap.assert_called_once_with(120, 340)


if __name__ == "__main__":
    unittest.main()
