import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import runtime_events


class RunScopedEventTests(unittest.TestCase):
    def test_acceptance_id_wins_over_untrusted_payload(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.object(runtime_events, "LOG_DIR", str(root)), \
                 patch.object(runtime_events, "RUN_LOG_DIR", str(root / "runs")), \
                 patch.object(runtime_events, "EVENT_FILE", str(root / "events.jsonl")), \
                 patch.dict(os.environ, {"TUGARIN_ACCEPTANCE_RUN_ID": "1234abcd"}):
                record = runtime_events.emit_event("begin", run_id="spoofed", message="safe")
                self.assertEqual(record["run_id"], "1234abcd")
                lines = (root / "runs" / "1234abcd.jsonl").read_text(encoding="utf-8").splitlines()
                self.assertEqual(json.loads(lines[0])["run_id"], "1234abcd")
                self.assertEqual(len(runtime_events.read_recent_events()), 1)

    def test_invalid_id_cannot_escape_run_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.object(runtime_events, "LOG_DIR", str(root)), \
                 patch.object(runtime_events, "RUN_LOG_DIR", str(root / "runs")), \
                 patch.object(runtime_events, "EVENT_FILE", str(root / "events.jsonl")), \
                 patch.dict(os.environ, {"TUGARIN_ACCEPTANCE_RUN_ID": "../../escape"}):
                record = runtime_events.emit_event("begin")
                self.assertNotIn("run_id", record)
                self.assertFalse((root / "runs").exists())

    def test_cumulative_rotation_does_not_remove_run_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "events.jsonl").write_text("old", encoding="utf-8")
            with patch.object(runtime_events, "LOG_DIR", str(root)), \
                 patch.object(runtime_events, "RUN_LOG_DIR", str(root / "runs")), \
                 patch.object(runtime_events, "EVENT_FILE", str(root / "events.jsonl")), \
                 patch.object(runtime_events, "MAX_CUMULATIVE_BYTES", 1), \
                 patch.dict(os.environ, {"TUGARIN_ACCEPTANCE_RUN_ID": "run5678"}):
                runtime_events.emit_event("new")
                self.assertEqual((root / "events.jsonl.1").read_text(encoding="utf-8"), "old")
                self.assertTrue((root / "runs" / "run5678.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
