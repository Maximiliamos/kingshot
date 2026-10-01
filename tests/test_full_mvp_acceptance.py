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

    def test_master_requires_final_dedicated_user_cleanup(self):
        self.assertIn("Final production-user process audit", SOURCE)
        self.assertIn("Production fast preview (WSA window or H.264)", SOURCE)
        self.assertIn("audit_runtime_processes.ps1", SOURCE)
        self.assertIn('"-TargetUser"', SOURCE)
        self.assertIn('"-TargetUser", $TargetUser', SOURCE)
        self.assertNotIn('"-TargetUser", [string]$env:USERNAME', SOURCE)
        self.assertIn("mvp-final-process-audit.json", SOURCE)

    def test_wrong_windows_sid_fails_before_evidence_or_destructive_gates(self):
        sid_check = SOURCE.index("$currentSid -ne $ExpectedSid")
        evidence_creation = SOURCE.index("$EvidencePath =")
        first_gate = SOURCE.index('Run-Gate -Name "Infrastructure')
        self.assertLess(sid_check, evidence_creation)
        self.assertLess(sid_check, first_gate)
        self.assertIn("exit 91", SOURCE)
        self.assertIn("S-1-5-21-1641294696-4270169483-3689275233-1007", SOURCE)

    def test_preflight_only_exits_before_any_gate(self):
        preflight = SOURCE.index("if ($PreflightOnly)")
        first_gate = SOURCE.index('Run-Gate -Name "Infrastructure')
        self.assertLess(preflight, first_gate)
        self.assertIn("MVP PRECHECK PASS", SOURCE)

    def test_master_persists_machine_readable_evidence(self):
        self.assertIn("mvp-full-acceptance.json", SOURCE)
        self.assertIn("Save-AcceptanceEvidence", SOURCE)
        self.assertIn('overall = $Overall', SOURCE)
        self.assertIn('head = $head', SOURCE)
        self.assertIn('gates = @($GateResults)', SOURCE)
        self.assertIn('-Overall "fail"', SOURCE)
        self.assertIn('-Overall "pass"', SOURCE)

    def test_master_requires_clean_tracked_worktree(self):
        self.assertIn("status --porcelain --untracked-files=no", SOURCE)
        self.assertIn("Clean tracked worktree", SOURCE)
        self.assertIn("exit 90", SOURCE)

    def test_destructive_gates_are_disclosed(self):
        self.assertIn("intentionally clear Kingshot app data", SOURCE)
        self.assertIn("nickname counter is preserved", SOURCE)


if __name__ == "__main__":
    unittest.main()
