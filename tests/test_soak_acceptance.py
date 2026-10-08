import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "scripts" / "verify_soak.ps1").read_text(encoding="utf-8-sig")


class SoakAcceptanceScriptTests(unittest.TestCase):
    def test_soak_runs_multiple_one_character_cycles(self):
        self.assertIn("prepare-mvp-soak", SOURCE)
        self.assertIn("soak-evidence", SOURCE)
        self.assertIn("MinCharacters", SOURCE)
        self.assertIn("[Math]::Max(2, $MinCharacters)", SOURCE)

    def test_soak_is_bounded_and_stops_gracefully(self):
        self.assertIn("TimeoutMinutes", SOURCE)
        self.assertLess(SOURCE.index("restriction-check"), SOURCE.index("prepare-mvp-soak"))
        self.assertIn("before pm clear", SOURCE)
        self.assertIn("requesting graceful stop", SOURCE)
        self.assertIn("control.json", SOURCE)
        self.assertIn("Stop-Process", SOURCE)

    def test_soak_records_resource_and_evidence_output(self):
        self.assertIn("WorkingSet64", SOURCE)
        self.assertIn("initial_bot_working_set_mb", SOURCE)
        self.assertIn("peak_bot_working_set_mb", SOURCE)
        self.assertIn("runtime-heartbeat.json", SOURCE)
        self.assertIn("stale_heartbeat_detected", SOURCE)
        self.assertIn("max_heartbeat_age_seconds", SOURCE)
        self.assertIn("mvp-soak-evidence.json", SOURCE)
        self.assertIn("MVP SOAK PASS", SOURCE)
        self.assertIn("MVP SOAK FAIL", SOURCE)


if __name__ == "__main__":
    unittest.main()
