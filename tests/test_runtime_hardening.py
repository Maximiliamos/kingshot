import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import runtime_events
import bot
from runtime_recovery import RecoveryController
from warbot_cli import _wait_package_stopped, _wait_production_frame


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
    def test_wait_package_stopped_handles_async_force_stop(self):
        backend = FakeBackend(FakeHealth(ready=True, package_running=True))
        states = iter((
            FakeHealth(ready=True, package_running=True),
            FakeHealth(ready=True, package_running=False),
        ))
        backend.health = lambda: next(states)
        with patch("warbot_cli.time.sleep"):
            self.assertTrue(_wait_package_stopped(backend, timeout=1.0))

    def test_wait_package_stopped_fails_closed_after_timeout(self):
        backend = FakeBackend(FakeHealth(ready=True, package_running=True))
        with patch("warbot_cli.time.monotonic", side_effect=(1.0, 2.0)):
            self.assertFalse(_wait_package_stopped(backend, timeout=1.0))

    def test_wait_production_frame_retries_until_window_exists(self):
        frame = type("Frame", (), {"size": 1})()
        capture = unittest.mock.MagicMock()
        capture.grab.return_value = (frame, 0.0, {})
        with patch(
            "warbot_cli.create_production_capture",
            side_effect=(RuntimeError("no window"), capture),
        ), patch("warbot_cli.time.sleep"):
            self.assertTrue(_wait_production_frame(object(), timeout=1.0))
        capture.close.assert_called_once_with()

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

    def test_control_file_accepts_windows_powershell_utf8_bom(self):
        with tempfile.TemporaryDirectory() as tmp:
            control = Path(tmp) / "control.json"
            control.write_bytes(
                b"\xef\xbb\xbf" + b'{"paused": false, "stop": true}'
            )
            with patch.object(bot, "CONTROL_FILE", str(control)):
                value = bot.load_control()
        self.assertFalse(value["paused"])
        self.assertTrue(value["stop"])

    def test_corrupt_state_is_preserved_and_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state.json"
            state.write_text("{broken", encoding="utf-8")
            previous = Path(tmp) / "state.previous.json"
            with patch.object(bot, "ROOT", tmp), \
                 patch.object(bot, "STATE_FILE", str(state)), \
                 patch.object(bot, "STATE_PREVIOUS_FILE", str(previous)):
                with self.assertRaises(RuntimeError):
                    bot.load_state()
            self.assertTrue(list(Path(tmp).glob("state.corrupt.*.json")))

    def test_state_save_keeps_previous_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state.json"
            previous = Path(tmp) / "state.previous.json"
            state.write_text('{"next_nickname": 4}', encoding="utf-8")
            with patch.object(bot, "STATE_FILE", str(state)), \
                 patch.object(bot, "STATE_PREVIOUS_FILE", str(previous)):
                bot.save_state({"next_nickname": 5})
            self.assertIn('"next_nickname": 4', previous.read_text(encoding="utf-8"))
            self.assertIn('"next_nickname": 5', state.read_text(encoding="utf-8"))

    def test_recovery_restarts_dead_game_only_after_threshold(self):
        backend = FakeBackend(FakeHealth(ready=True, package_running=False))
        controller = RecoveryController(capture_threshold=2, max_game_restarts=1)
        first = controller.capture_failed(backend, "frame")
        second = controller.capture_failed(backend, "frame")
        self.assertEqual(first.action, "retry_capture")
        self.assertEqual(second.action, "game_restarted")
        self.assertEqual(backend.launched, 1)
        self.assertEqual(backend.waited, 1)

    def test_periodic_probe_restarts_dead_game_without_capture_failure(self):
        backend = FakeBackend(FakeHealth(ready=True, package_running=False))
        controller = RecoveryController(max_game_restarts=1)
        decision = controller.probe_runtime(backend)
        self.assertEqual(decision.action, "game_restarted")
        self.assertFalse(decision.terminal)
        self.assertEqual(backend.launched, 1)
        self.assertEqual(backend.waited, 1)

    def test_periodic_probe_exhausts_restart_budget(self):
        backend = FakeBackend(FakeHealth(ready=True, package_running=False))
        controller = RecoveryController(max_game_restarts=1)
        first = controller.probe_runtime(backend)
        second = controller.probe_runtime(backend)
        self.assertEqual(first.action, "game_restarted")
        self.assertTrue(second.terminal)
        self.assertEqual(second.action, "stop")

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
