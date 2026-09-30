import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "scripts" / "verify_game_flow.ps1").read_text(encoding="utf-8-sig")


class GameFlowAcceptanceScriptTests(unittest.TestCase):
    def test_flow_is_explicitly_destructive_and_bounded(self):
        self.assertIn("intentionally clears Kingshot app data", SOURCE)
        self.assertIn("prepare-mvp-flow", SOURCE)
        self.assertIn("--yes", SOURCE)
        self.assertIn("TimeoutMinutes", SOURCE)
        self.assertIn("Stop-Process", SOURCE)

    def test_flow_requires_machine_readable_evidence(self):
        self.assertIn("flow-evidence", SOURCE)
        self.assertIn("mvp-game-flow-evidence.json", SOURCE)
        self.assertIn("MVP GAME FLOW PASS", SOURCE)
        self.assertIn("MVP GAME FLOW FAIL", SOURCE)

    def test_flow_refuses_parallel_gui_or_bot(self):
        self.assertIn("Get-CimInstance Win32_Process", SOURCE)
        self.assertIn("gui", SOURCE)
        self.assertIn("bot", SOURCE)


if __name__ == "__main__":
    unittest.main()
