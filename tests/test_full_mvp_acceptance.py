import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "scripts" / "verify_mvp_full.ps1").read_text(encoding="utf-8-sig")


class FullMvpAcceptanceScriptTests(unittest.TestCase):
    def test_master_runs_all_release_gates(self):
        for name in (
            "verify_release.ps1",
            "verify_preview.ps1",
            "verify_gui.ps1",
            "verify_operator_io.ps1",
            "verify_recovery.ps1",
            "verify_game_flow.ps1",
            "verify_soak.ps1",
        ):
            self.assertIn(name, SOURCE)

    def test_master_is_fail_fast(self):
        self.assertIn("MVP 1.0 HOST ACCEPTANCE FAIL", SOURCE)
        self.assertIn("exit $code", SOURCE)
        self.assertIn("MVP 1.0 HOST ACCEPTANCE PASS", SOURCE)

    def test_destructive_gates_are_disclosed(self):
        self.assertIn("intentionally clear Kingshot app data", SOURCE)
        self.assertIn("nickname counter is preserved", SOURCE)


if __name__ == "__main__":
    unittest.main()
