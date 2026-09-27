import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

import device_backend as db


class DeviceBackendTests(unittest.TestCase):
    def test_health_native_arm64_gate(self):
        health = db.DeviceHealth(
            backend="native_arm64",
            serial="127.0.0.1:5561",
            state="device",
            boot_completed="1",
            abi="arm64-v8a",
            abilist="arm64-v8a,armeabi-v7a",
            native_bridge="",
        )
        self.assertTrue(health.ready)
        self.assertTrue(health.native_arm64)

    def test_health_rejects_x86_or_native_bridge(self):
        x86 = db.DeviceHealth(
            backend="adb",
            serial="x",
            state="device",
            boot_completed="1",
            abi="arm64-v8a",
            abilist="x86_64,arm64-v8a",
        )
        bridge = db.DeviceHealth(
            backend="adb",
            serial="x",
            state="device",
            boot_completed="1",
            abi="arm64-v8a",
            abilist="arm64-v8a",
            native_bridge="libhoudini.so",
        )
        self.assertFalse(x86.native_arm64)
        self.assertFalse(bridge.native_arm64)

    def test_factory_defaults_to_native_arm64(self):
        with patch.dict("os.environ", {}, clear=True):
            backend = db.create_backend(adb_path=r"C:\fake\adb.exe")
        self.assertIsInstance(backend, db.NativeArm64Backend)

    def test_factory_supports_plain_adb(self):
        backend = db.create_backend(
            "adb",
            serial="device-1",
            adb_path=r"C:\fake\adb.exe",
        )
        self.assertIsInstance(backend, db.AdbDeviceBackend)
        self.assertEqual(backend.serial, "device-1")

    def test_screenshot_decodes_png(self):
        backend = db.AdbDeviceBackend(
            serial="device-1",
            adb_path=r"C:\fake\adb.exe",
        )
        image = np.zeros((20, 10, 3), dtype=np.uint8)
        ok, encoded = cv2.imencode(".png", image)
        self.assertTrue(ok)
        result = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=encoded.tobytes(),
            stderr=b"",
        )
        with patch.object(backend, "_run", return_value=result):
            frame = backend.frame()
        self.assertEqual(frame.shape, (20, 10, 3))

    def test_input_text_fails_closed_for_cyrillic(self):
        backend = db.AdbDeviceBackend(
            serial="device-1",
            adb_path=r"C:\fake\adb.exe",
        )
        with self.assertRaises(db.BackendError):
            backend.input_text("Тугарин1")

    def test_backend_capture_keeps_old_capture_contract(self):
        class FakeBackend(db.DeviceBackend):
            backend_name = "fake"

            def __init__(self):
                super().__init__(serial="serial")

            def health(self):
                return db.DeviceHealth("fake", "serial", "device", "1")

            def frame(self):
                return np.zeros((30, 15, 3), dtype=np.uint8)

            def tap(self, x, y):
                pass

            def swipe(self, x1, y1, x2, y2, duration_ms):
                pass

            def keyevent(self, code):
                pass

            def shell(self, args, *, timeout=60):
                return ""

        capture = db.BackendCapture(FakeBackend())
        frame, title, rect = capture.grab()
        self.assertEqual(frame.shape, (30, 15, 3))
        self.assertEqual(title, "fake:serial")
        self.assertEqual(rect["width"], 15)
        self.assertEqual(rect["height"], 30)


if __name__ == "__main__":
    unittest.main()
