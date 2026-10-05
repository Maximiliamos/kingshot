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


if __name__ == "__main__":
    unittest.main()
