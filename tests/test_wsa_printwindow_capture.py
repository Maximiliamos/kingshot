import unittest

import bot


class WsaPrintWindowCaptureTests(unittest.TestCase):
    def test_capture_releases_gdi_handles_when_printwindow_fails(self):
        capture = bot.WsaGameWindowCapture()
        capture.hwnd = 123
        calls = []

        class FakeUser32:
            def GetDC(self, hwnd):
                calls.append(("getdc", hwnd))
                return 10

            def PrintWindow(self, hwnd, dc, flags):
                calls.append(("printwindow", hwnd, dc, flags))
                return 0

            def ReleaseDC(self, hwnd, dc):
                calls.append(("releasedc", hwnd, dc))
                return 1

        class FakeGdi32:
            def CreateCompatibleDC(self, dc):
                calls.append(("createdc", dc))
                return 20

            def CreateCompatibleBitmap(self, dc, width, height):
                calls.append(("bitmap", dc, width, height))
                return 30

            def SelectObject(self, dc, obj):
                calls.append(("select", dc, obj))
                return 40

            def DeleteObject(self, obj):
                calls.append(("deleteobject", obj))
                return 1

            def DeleteDC(self, dc):
                calls.append(("deletedc", dc))
                return 1

        with self.assertRaisesRegex(RuntimeError, "PrintWindow"):
            capture._capture_client(
                20,
                30,
                user32_api=FakeUser32(),
                gdi32_api=FakeGdi32(),
            )

        self.assertIn(("select", 20, 40), calls)
        self.assertIn(("deleteobject", 30), calls)
        self.assertIn(("deletedc", 20), calls)
        self.assertIn(("releasedc", 123, 10), calls)

    def test_capture_releases_partial_handles_when_bitmap_creation_fails(self):
        capture = bot.WsaGameWindowCapture()
        capture.hwnd = 456
        calls = []

        class FakeUser32:
            def GetDC(self, hwnd):
                return 11

            def ReleaseDC(self, hwnd, dc):
                calls.append(("releasedc", hwnd, dc))
                return 1

        class FakeGdi32:
            def CreateCompatibleDC(self, dc):
                return 22

            def CreateCompatibleBitmap(self, dc, width, height):
                return 0

            def DeleteDC(self, dc):
                calls.append(("deletedc", dc))
                return 1

        with self.assertRaisesRegex(RuntimeError, "CreateCompatibleBitmap"):
            capture._capture_client(
                20,
                30,
                user32_api=FakeUser32(),
                gdi32_api=FakeGdi32(),
            )

        self.assertEqual(calls, [("deletedc", 22), ("releasedc", 456, 11)])

    def test_printwindow_transport_is_explicitly_identified(self):
        self.assertEqual(bot.WsaGameWindowCapture.transport_name, "wsa-window")
        self.assertEqual(bot.WsaGameWindowCapture.capture_method, "printwindow")


if __name__ == "__main__":
    unittest.main()
