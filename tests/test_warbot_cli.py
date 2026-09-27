import io
import unittest
from tempfile import TemporaryDirectory
from pathlib import Path

import numpy as np
from contextlib import redirect_stdout
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

    def test_clear_game_data_requires_explicit_yes(self):
        backend = MagicMock()
        with patch("warbot_cli.create_backend", return_value=backend):
            with self.assertRaises(BackendError):
                warbot_cli.main(["clear-game-data", "--backend", "adb"])
        backend.clear_app_data.assert_not_called()

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
                    patch.object(backend, "package_installed", return_value=False), \
                    patch.object(backend, "install_verified_game", return_value="Success") as install, \
                    patch.object(backend, "launch_app", return_value="Starting"), \
                    patch.object(backend, "wait_package_running", return_value="1234"), \
                    patch.object(backend, "frame", return_value=np.zeros((20, 10, 3), dtype=np.uint8)):
                out = io.StringIO()
                with redirect_stdout(out):
                    code = warbot_cli.main(["bootstrap", "--output", output])

            self.assertEqual(code, 0)
            install.assert_called_once()
            self.assertTrue(Path(output).is_file())
            self.assertIn('"native_arm64": true', out.getvalue())

    def test_tap_routes_coordinates_to_backend(self):
        backend = MagicMock()
        with patch("warbot_cli.create_backend", return_value=backend):
            code = warbot_cli.main(["tap", "--backend", "adb", "120", "340"])
        self.assertEqual(code, 0)
        backend.tap.assert_called_once_with(120, 340)


if __name__ == "__main__":
    unittest.main()
