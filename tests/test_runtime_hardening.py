import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import runtime_events
from runtime_recovery import RecoveryController


class FakeHealth:
    def __init__(self, *, ready=True, package_running=True):
        self.ready = ready
        self.package_running = package_running


class FakeBackend:
    def __init__(self, health):
        self._health = health
        self.launched = 0
        self.waited = 0

    def health(self):
        return self._health

    def launch_app(self):
        self.launched += 1

    def wait_package_running(self, timeout=45):
        self.waited += 1
        return "123"


class RuntimeHardeningTests(unittest.TestCase):
    def test_structured_event_log_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            event_path = str(Path(tmp) / "events.jsonl")
            with patch.object(runtime_events, "EVENT_FILE", event_path), \
                 patch.object(runtime_events, "LOG_DIR", tmp):
                runtime_events.emit_event("state_transition", phase="tutorial")
                items = runtime_events.read_recent_events()
        self.assertEqual(items[-1]["event"], "state_transition")
        self.assertEqual(items[-1]["phase"], "tutorial")
        self.assertIn("ts", items[-1])

    def test_recovery_restarts_dead_game_only_after_threshold(self):
        backend = FakeBackend(FakeHealth(ready=True, package_running=False))
        controller = RecoveryController(capture_threshold=2, max_game_restarts=1)
        first = controller.capture_failed(backend, "frame")
        second = controller.capture_failed(backend, "frame")
        self.assertEqual(first.action, "retry_capture")
        self.assertEqual(second.action, "game_restarted")
        self.assertEqual(backend.launched, 1)
        self.assertEqual(backend.waited, 1)

    def test_recovery_is_bounded_when_runtime_stays_down(self):
        backend = FakeBackend(FakeHealth(ready=False, package_running=False))
        controller = RecoveryController(capture_threshold=1, max_runtime_failures=2)
        first = controller.capture_failed(backend, "adb")
        second = controller.capture_failed(backend, "adb")
        self.assertEqual(first.action, "wait_runtime")
        self.assertTrue(second.terminal)
        self.assertEqual(second.action, "stop")


if __name__ == "__main__":
    unittest.main()
