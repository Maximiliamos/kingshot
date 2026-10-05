import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "scripts" / "verify_recovery.ps1").read_text(encoding="utf-8-sig")


class RecoveryAcceptanceScriptTests(unittest.TestCase):
    def test_recovery_gate_injects_game_and_adb_failures(self):
        self.assertIn("recovery-smoke", SOURCE)
        self.assertIn("--with-adb-reconnect", SOURCE)
        self.assertIn("RECOVERY HOST GATE PASS", SOURCE)
        self.assertIn("RECOVERY HOST GATE FAIL", SOURCE)

    def test_recovery_gate_has_no_scrcpy_runtime_dependency(self):
        self.assertNotIn("provision_scrcpy_server.ps1", SOURCE)
        self.assertIn("recovery-smoke.json", SOURCE)


if __name__ == "__main__":
    unittest.main()
