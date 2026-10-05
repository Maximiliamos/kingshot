import unittest
from unittest.mock import patch

import bot


class FakeUser32:
    def __init__(self, print_ok=True):
        self.print_ok = print_ok
        self.released = []

    def GetDC(self, hwnd):
        return 101

    def PrintWindow(self, hwnd, dc, flags):
        return 1 if self.print_ok else 0

    def ReleaseDC(self, hwnd, dc):
        self.released.append((hwnd, dc))
        return 1


class FakeGdi32:
    def __init__(self, bitmap_ok=True):
        self.bitmap_ok = bitmap_ok
        self.selected = []
        self.deleted_objects = []
        self.deleted_dcs = []

    def CreateCompatibleDC(self, dc):
        return 202

    def CreateCompatibleBitmap(self, dc, width, height):
        return 303 if self.bitmap_ok else 0

    def SelectObject(self, dc, obj):
        self.selected.append((dc, obj))
        return 404

    def GetDIBits(self, dc, bitmap, start, height, pixels, header, usage):
        for index in range(len(pixels)):
            pixels[index] = 255
        return 1

    def DeleteObject(self, obj):
        self.deleted_objects.append(obj)
        return 1

    def DeleteDC(self, dc):
        self.deleted_dcs.append(dc)
        return 1


class PrintWindowCaptureTests(unittest.TestCase):
    def _capture(self):
        capture = bot.WsaGameWindowCapture()
        capture.hwnd = 77
        capture.rect = {"left": 10, "top": 20, "width": 4, "height": 3}
        return capture

    def test_printwindow_success_releases_every_gdi_handle(self):
        capture = self._capture()
        user32 = FakeUser32()
        gdi32 = FakeGdi32()
        frame = capture._capture_client(4, 3, user32_api=user32, gdi32_api=gdi32)
        self.assertEqual(frame.shape, (3, 4, 3))
        self.assertEqual(gdi32.deleted_objects, [303])
        self.assertEqual(gdi32.deleted_dcs, [202])
        self.assertEqual(user32.released, [(77, 101)])
        self.assertEqual(gdi32.selected[-1], (202, 404))

    def test_printwindow_failure_still_releases_handles(self):
        capture = self._capture()
        user32 = FakeUser32(print_ok=False)
        gdi32 = FakeGdi32()
        with self.assertRaises(RuntimeError):
            capture._capture_client(4, 3, user32_api=user32, gdi32_api=gdi32)
        self.assertEqual(gdi32.deleted_objects, [303])
        self.assertEqual(gdi32.deleted_dcs, [202])
        self.assertEqual(user32.released, [(77, 101)])

    def test_bitmap_creation_failure_releases_partial_allocation(self):
        capture = self._capture()
        user32 = FakeUser32()
        gdi32 = FakeGdi32(bitmap_ok=False)
        with self.assertRaises(RuntimeError):
            capture._capture_client(4, 3, user32_api=user32, gdi32_api=gdi32)
        self.assertEqual(gdi32.deleted_objects, [])
        self.assertEqual(gdi32.deleted_dcs, [202])
        self.assertEqual(user32.released, [(77, 101)])

    def test_automation_uses_printwindow_as_wsa_production_capture(self):
        with patch.object(bot, "BACKEND_NAME", "wsa"):
            capture = bot.create_capture()
        self.assertIsInstance(capture, bot.WsaGameWindowCapture)
        self.assertEqual(capture.capture_method, "printwindow")


if __name__ == "__main__":
    unittest.main()
