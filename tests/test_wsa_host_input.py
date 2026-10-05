import unittest
from unittest.mock import patch

import device_backend as db


class FakeUser32:
    def __init__(self, fail_position_call=None):
        self.foreground = 900
        self.foreground_calls = []
        self.position_calls = []
        self.events = []
        self.fail_position_call = fail_position_call

    def GetForegroundWindow(self):
        return self.foreground

    def GetCursorPos(self, ptr):
        ptr._obj.x = 7
        ptr._obj.y = 9
        return 1

    def SetForegroundWindow(self, hwnd):
        self.foreground_calls.append(hwnd)
        return 1

    def SetCursorPos(self, x, y):
        self.position_calls.append((x, y))
        if self.fail_position_call == len(self.position_calls):
            return 0
        return 1

    def mouse_event(self, flag, dx, dy, data, extra):
        self.events.append(flag)


class FakeAttachedUser32(FakeUser32):
    def __init__(self):
        super().__init__()
        self.attached = False
        self.attach_calls = []

    def SetForegroundWindow(self, hwnd):
        self.foreground_calls.append(hwnd)
        if not self.attached:
            return 0
        self.foreground = hwnd
        return 1

    def GetWindowThreadProcessId(self, hwnd, _):
        return int(hwnd) + 1000

    def AttachThreadInput(self, current, target, attach):
        self.attach_calls.append((current, target, bool(attach)))
        self.attached = bool(attach)
        return 1

    def ShowWindow(self, hwnd, mode):
        return 1

    def BringWindowToTop(self, hwnd):
        return 1


class FakeKernel32:
    def GetCurrentThreadId(self):
        return 77


class WsaHostInputTests(unittest.TestCase):
    def _backend(self):
        return db.WsaBackend(
            serial="127.0.0.1:58526",
            adb_path=r"C:\\fake\\adb.exe",
        )

    def test_client_coordinates_are_offset_to_desktop_and_restored(self):
        backend = self._backend()
        api = FakeUser32()
        with patch.object(backend, "_game_window", return_value=(55, 100, 200, 800, 600)), \
                patch.object(backend, "_window_user32", return_value=api), \
                patch.object(db.time, "sleep", return_value=None):
            ok = backend._post_window_pointer([(10, 20)], 50)
        self.assertTrue(ok)
        self.assertEqual(api.position_calls[0], (110, 220))
        self.assertEqual(api.position_calls[-1], (7, 9))
        self.assertEqual(api.foreground_calls, [55, 900])
        self.assertEqual(api.events, [0x0002, 0x0004])

    def test_background_process_temporarily_attaches_ui_threads(self):
        backend = self._backend()
        api = FakeAttachedUser32()
        with patch.object(backend, "_window_kernel32", return_value=FakeKernel32()):
            self.assertTrue(backend._set_foreground_window(api, 55))
        self.assertEqual(api.foreground, 55)
        self.assertTrue(any(call[2] for call in api.attach_calls))
        self.assertTrue(any(not call[2] for call in api.attach_calls))

    def test_failed_swipe_releases_button_and_restores_user_state(self):
        backend = self._backend()
        api = FakeUser32(fail_position_call=2)
        with patch.object(backend, "_game_window", return_value=(55, 100, 200, 800, 600)), \
                patch.object(backend, "_window_user32", return_value=api), \
                patch.object(db.time, "sleep", return_value=None):
            ok = backend._post_window_pointer([(10, 20), (20, 30)], 100)
        self.assertFalse(ok)
        self.assertEqual(api.events[0], 0x0002)
        self.assertEqual(api.events[-1], 0x0004)
        self.assertEqual(api.position_calls[-1], (7, 9))
        self.assertEqual(api.foreground_calls[-1], 900)

    def test_out_of_client_bounds_is_rejected_before_pointer_injection(self):
        backend = self._backend()
        api = FakeUser32()
        with patch.object(backend, "_game_window", return_value=(55, 100, 200, 800, 600)), \
                patch.object(backend, "_window_user32", return_value=api):
            ok = backend._post_window_pointer([(801, 20)], 50)
        self.assertFalse(ok)
        self.assertEqual(api.position_calls, [])
        self.assertEqual(api.events, [])

    def test_public_tap_uses_host_pointer_and_never_adb_fallback(self):
        backend = self._backend()
        with patch.object(backend, "_post_window_pointer", return_value=True) as post, \
                patch.object(db.AdbDeviceBackend, "tap") as adb_tap:
            backend.tap(25, 30)
        post.assert_called_once_with([(25, 30)])
        adb_tap.assert_not_called()

    def test_public_hold_uses_stationary_host_swipe(self):
        backend = self._backend()
        with patch.object(backend, "_post_window_pointer", return_value=True) as post:
            backend.hold(40, 50, 800)
        points, duration = post.call_args.args
        self.assertEqual(duration, 800)
        self.assertGreaterEqual(len(points), 4)
        self.assertTrue(all(point == (40, 50) for point in points))

    def test_public_swipe_uses_host_pointer_path(self):
        backend = self._backend()
        with patch.object(backend, "_post_window_pointer", return_value=True) as post:
            backend.swipe(10, 20, 110, 220, 500)
        points, duration = post.call_args.args
        self.assertEqual(duration, 500)
        self.assertEqual(points[0], (10, 20))
        self.assertEqual(points[-1], (110, 220))

    def test_public_pointer_actions_fail_closed_when_host_window_is_unavailable(self):
        backend = self._backend()
        with patch.object(backend, "_post_window_pointer", return_value=False):
            with self.assertRaises(db.BackendError):
                backend.tap(10, 20)
            with self.assertRaises(db.BackendError):
                backend.hold(10, 20, 800)
            with self.assertRaises(db.BackendError):
                backend.swipe(10, 20, 30, 40, 300)


if __name__ == "__main__":
    unittest.main()
