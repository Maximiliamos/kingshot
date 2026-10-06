import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "scripts" / "run_full_mvp_and_report.ps1").read_text(
    encoding="utf-8-sig"
)
INSTALL = (ROOT / "scripts" / "install.ps1").read_text(encoding="utf-8-sig")


class FullMvpReporterTests(unittest.TestCase):
    def test_runner_refuses_wrong_local_branch_before_pull(self):
        self.assertIn("git branch --show-current", SOURCE)
        self.assertIn("Refusing full MVP acceptance on branch", SOURCE)
        self.assertIn("$currentBranch -ne $Branch", SOURCE)

    def test_runner_pulls_exact_published_branch_and_reexecs_after_update(self):
        self.assertIn("git pull --ff-only origin $Branch", SOURCE)
        self.assertIn('symbolic-full-name "@{u}"', SOURCE)
        self.assertIn("Local HEAD must equal published upstream", SOURCE)
        self.assertIn("TUGARIN_FULL_REPORT_REEXEC", SOURCE)

    def test_runner_can_continue_after_transient_git_tls_failure_only_on_pinned_sha(self):
        self.assertIn('[string]$ExpectedCommit = ""', SOURCE)
        self.assertIn("local HEAD exactly matches pinned release SHA", SOURCE)
        self.assertIn("git pull --ff-only failed and local HEAD is not protected by -ExpectedCommit", SOURCE)
        self.assertIn("unexpected SHA", SOURCE)
        self.assertIn("Published branch moved away from pinned release SHA", SOURCE)
        self.assertIn("expected_commit = $ExpectedCommit", SOURCE)

    def test_runner_does_not_promote_optional_git_stderr_to_terminating_error(self):
        self.assertIn('$savedErrorAction = $ErrorActionPreference', SOURCE)
        self.assertIn('$ErrorActionPreference = "Continue"', SOURCE)
        self.assertIn('$pullCode = [int]$LASTEXITCODE', SOURCE)
        self.assertIn('$ErrorActionPreference = $savedErrorAction', SOURCE)

    def test_runner_executes_full_acceptance_not_p0_only(self):
        self.assertIn("verify_mvp_full.ps1", SOURCE)
        self.assertNotIn("install_wsa_poc.ps1", SOURCE)
        self.assertIn("-FlowTimeoutMinutes", SOURCE)
        self.assertIn("-SoakTimeoutMinutes", SOURCE)
        self.assertIn("-SoakCharacters", SOURCE)

    def test_runner_collects_every_release_evidence_file(self):
        for name in (
            "mvp-full-acceptance.json",
            "google-services.json",
            "preview-production.json",
            "preview-production.png",
            "gui-host-smoke.json",
            "operator-io-smoke.json",
            "recovery-smoke.json",
            "mvp-game-flow-evidence.json",
            "mvp-soak-evidence.json",
            "mvp-final-process-audit.json",
            "runtime-heartbeat.json",
            "android-data-free-space.json",
            "tutorial-perception-failure.json",
            "events.jsonl",
        ):
            self.assertIn(name, SOURCE)

    def test_runner_collects_tutorial_perception_images_from_json(self):
        self.assertIn('"full_frame"', SOURCE)
        self.assertIn('"normalized_frame"', SOURCE)
        self.assertIn('"annotated_frame"', SOURCE)

    def test_runner_clears_previous_release_evidence_before_verifier(self):
        self.assertIn("Clear-PreviousRunEvidence", SOURCE)
        self.assertIn("Remove-Item", SOURCE)
        self.assertIn("mvp-failure-process-audit.json", SOURCE)
        self.assertLess(
            SOURCE.index("Clear-PreviousRunEvidence\n\n$savedErrorAction"),
            SOURCE.index("& powershell.exe @verifyArgs"),
        )

    def test_runner_requires_pass_evidence_for_current_head_and_run_id(self):
        self.assertIn("$acceptanceHead -eq $commit", SOURCE)
        self.assertIn("$acceptanceRunId", SOURCE)
        self.assertIn("acceptance_run_id = $acceptanceRunId", SOURCE)
        self.assertIn("acceptance_head = $acceptanceHead", SOURCE)
        self.assertIn("does not prove PASS for current HEAD/run_id", SOURCE)

    def test_runner_hashes_collected_evidence(self):
        self.assertIn("Get-FileHash", SOURCE)
        self.assertIn("evidence_files", SOURCE)
        self.assertIn("sha256", SOURCE)
        self.assertIn("size_bytes", SOURCE)

    def test_runner_uploads_failed_or_passed_run_to_runtime_reports(self):
        self.assertIn("upload_runtime_report.ps1", SOURCE)
        self.assertIn('kind = "mvp-full-host"', SOURCE)
        self.assertIn("verify_exit_code", SOURCE)
        self.assertIn("overall = $overall", SOURCE)
        self.assertIn("exit $verifyExit", SOURCE)

    def test_release_installer_ships_full_reporter(self):
        self.assertIn("run_full_mvp_and_report.ps1", INSTALL)


if __name__ == "__main__":
    unittest.main()
