import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bot
import runtime_events
from runtime_recovery import RecoveryController


class Health:
    ready = True
    package_running = True


class Backend:
    def health(self):
        return Health()


class LongRunLiteTests(unittest.TestCase):
    def test_250_atomic_state_updates_preserve_latest_and_previous(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state.json"
            previous = Path(tmp) / "state.previous.json"
            with patch.object(bot, "STATE_FILE", str(state)), \
                 patch.object(bot, "STATE_PREVIOUS_FILE", str(previous)), \
                 patch.object(bot, "ensure_dirs", lambda: None):
                for index in range(250):
                    bot.save_state({
                        "phase": "tutorial_new_character",
                        "step": "tutorial_intro",
                        "next_nickname": index + 1,
                    })

            latest_payload = json.loads(state.read_text(encoding="utf-8"))
            previous_payload = json.loads(previous.read_text(encoding="utf-8"))

        self.assertEqual(latest_payload["next_nickname"], 250)
        self.assertEqual(previous_payload["next_nickname"], 249)
        self.assertFalse(state.with_suffix(".json.tmp").exists())

    def test_1000_structured_events_remain_parseable(self):
        with tempfile.TemporaryDirectory() as tmp:
            event_path = str(Path(tmp) / "events.jsonl")
            with patch.object(runtime_events, "EVENT_FILE", event_path), \
                 patch.object(runtime_events, "LOG_DIR", tmp):
                for index in range(1000):
                    runtime_events.emit_event("tick", sequence=index)
                recent = runtime_events.read_recent_events(100)
        self.assertEqual(len(recent), 100)
        self.assertEqual(recent[0]["sequence"], 900)
        self.assertEqual(recent[-1]["sequence"], 999)

    def test_recovery_counter_resets_after_healthy_frame(self):
        controller = RecoveryController(capture_threshold=3)
        backend = Backend()
        controller.capture_failed(backend, "one")
        controller.capture_failed(backend, "two")
        self.assertEqual(controller.capture_failures, 2)
        controller.capture_succeeded()
        self.assertEqual(controller.capture_failures, 0)
        self.assertEqual(controller.runtime_failures, 0)


if __name__ == "__main__":
    unittest.main()
