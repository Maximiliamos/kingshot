import io
import unittest
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

    def test_tap_routes_coordinates_to_backend(self):
        backend = MagicMock()
        with patch("warbot_cli.create_backend", return_value=backend):
            code = warbot_cli.main(["tap", "--backend", "adb", "120", "340"])
        self.assertEqual(code, 0)
        backend.tap.assert_called_once_with(120, 340)


if __name__ == "__main__":
    unittest.main()
