import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "scripts" / "verify_mvp_full.ps1").read_text(encoding="utf-8-sig")


class FullMvpAcceptanceScriptTests(unittest.TestCase):
    def test_acceptance_evidence_writer_cannot_shadow_gate_output_path(self):
        # PowerShell function lookups are dynamically scoped and
        # case-insensitive. A local $evidencePath in Run-Gate must never make
        # Save-AcceptanceEvidence write over gui-host-smoke.json, etc.
        self.assertIn('$script:EvidencePath', SOURCE)
        self.assertIn('${script:EvidencePath}.tmp', SOURCE)
        self.assertIn('foreach ($gateEvidencePath in @($EvidencePaths))', SOURCE)
        self.assertNotIn('foreach ($evidencePath in @($EvidencePaths))', SOURCE)

    def test_gate_requires_parsed_per_gate_success_evidence(self):
        self.assertIn('$verifiedGateJson', SOURCE)
        self.assertIn('[bool]$gateEvidence.pass', SOURCE)
        self.assertIn('pass=true missing or false', SOURCE)
        self.assertIn('invalid or non-PASS evidence JSON', SOURCE)
        self.assertIn('$code = 94', SOURCE)

    def test_master_runs_all_release_gates(self):
        for name in (
            "verify_release.ps1",
            "verify_google_services.ps1",
            "verify_preview.ps1",
            "verify_gui.ps1",
            "verify_operator_io.ps1",
            "verify_recovery.ps1",
            "verify_resource_readiness.ps1",
            "verify_replay_regression.ps1",
            "verify_game_flow.ps1",
            "verify_soak.ps1",
        ):
            self.assertIn(name, SOURCE)

    def test_resource_readiness_precedes_any_destructive_game_flow(self):
        self.assertIn("resource-readiness.json", SOURCE)
        self.assertLess(
            SOURCE.index('Run-Gate -Name "Non-destructive resource readiness"'),
            SOURCE.index('Run-Gate -Name "Exact State #3'),
        )
        gate = (ROOT / "scripts" / "verify_resource_readiness.ps1").read_text(encoding="utf-8")
        self.assertIn("resource-readiness", gate)
        self.assertNotIn("clear-game-data", gate)
        self.assertNotIn("prepare-mvp-flow", gate)
        self.assertNotIn("prepare-mvp-soak", gate)

    def test_real_frame_replay_is_a_blocking_release_gate(self):
        self.assertIn("replay-regression.json", SOURCE)
        self.assertLess(
            SOURCE.index('Run-Gate -Name "Real-frame tutorial replay regression"'),
            SOURCE.index('Run-Gate -Name "Exact State #3'),
        )
        gate = (ROOT / "scripts" / "verify_replay_regression.ps1").read_text(encoding="utf-8")
        self.assertIn("--require-real-coverage", gate)

    def test_master_is_fail_fast(self):
        self.assertIn("MVP 1.0 HOST ACCEPTANCE FAIL", SOURCE)
        self.assertIn("exit $code", SOURCE)
        self.assertIn("MVP 1.0 HOST ACCEPTANCE PASS", SOURCE)

    def test_master_requires_final_dedicated_user_cleanup(self):
        self.assertIn("Final production-user process audit", SOURCE)
        self.assertIn("Production PrintWindow preview", SOURCE)
        self.assertIn("Google Services / Play Store / account", SOURCE)
        self.assertIn("preview-production.json", SOURCE)
        self.assertIn("google-services.json", SOURCE)
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

    def test_preflight_is_windows_powershell_51_safe_for_unicode_account_name(self):
        self.assertIn('[string]$TargetUser = ""', SOURCE)
        self.assertIn("SecurityIdentifier -ArgumentList $ExpectedSid", SOURCE)
        self.assertIn("Translate(", SOURCE)
        self.assertIn("$resolvedTargetUser", SOURCE)
        self.assertNotIn('TargetUser = "Программист1"', SOURCE)

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

    def test_master_requires_exact_published_upstream_head(self):
        self.assertIn("Published branch head", SOURCE)
        self.assertIn('symbolic-full-name "@{u}"', SOURCE)
        self.assertIn("exit 92", SOURCE)

    def test_failure_process_audit_returns_only_numeric_exit_code(self):
        self.assertIn("audit_runtime_processes.ps1", SOURCE)
        self.assertIn("| Out-Host", SOURCE)
        self.assertIn("$auditCode = [int]$LASTEXITCODE", SOURCE)
        self.assertIn("return $auditCode", SOURCE)

    def test_gate_pass_requires_expected_evidence_files(self):
        self.assertIn("missing_evidence", SOURCE)
        self.assertIn("required evidence file(s) missing", SOURCE)
        self.assertIn("exit_code = $code", SOURCE)
        self.assertIn("$code = 93", SOURCE)

    def test_destructive_gates_are_disclosed(self):
        self.assertIn("subsequently clear Kingshot app data", SOURCE)
        self.assertIn("nickname counter is preserved", SOURCE)


if __name__ == "__main__":
    unittest.main()
