import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "scripts" / "run_full_mvp_and_report.ps1").read_text(
    encoding="utf-8-sig"
)
INSTALL = (ROOT / "scripts" / "install.ps1").read_text(encoding="utf-8-sig")


class FullMvpReporterTests(unittest.TestCase):
    def test_runner_pulls_exact_published_branch_and_reexecs_after_update(self):
        self.assertIn("git pull --ff-only origin $Branch", SOURCE)
        self.assertIn('symbolic-full-name "@{u}"', SOURCE)
        self.assertIn("Local HEAD must equal published upstream", SOURCE)
        self.assertIn("TUGARIN_FULL_REPORT_REEXEC", SOURCE)

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
            "events.jsonl",
        ):
            self.assertIn(name, SOURCE)

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
