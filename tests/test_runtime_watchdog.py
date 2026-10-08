import json
import tempfile
import unittest
from pathlib import Path

from runtime_watchdog import RuntimeHeartbeat


class RuntimeHeartbeatTests(unittest.TestCase):
    def test_heartbeat_is_atomic_throttled_and_contains_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "heartbeat.json")
            heartbeat = RuntimeHeartbeat(path=path, interval=60)
            heartbeat.mark_frame()
            heartbeat.mark_action()
            self.assertTrue(
                heartbeat.write(
                    state={"phase": "tutorial_new_character", "step": "tutorial_intro"},
                    backend="wsa",
                    serial="127.0.0.1:58526",
                )
            )
            self.assertFalse(
                heartbeat.write(
                    state={"phase": "x", "step": "y"},
                    backend="wsa",
                    serial="127.0.0.1:58526",
                )
            )
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        self.assertEqual(payload["phase"], "tutorial_new_character")
        self.assertEqual(payload["step"], "tutorial_intro")
        self.assertEqual(payload["backend"], "wsa")
        self.assertEqual(payload["status"], "running")
        self.assertIsNotNone(payload["last_frame_age_seconds"])


if __name__ == "__main__":
    unittest.main()
