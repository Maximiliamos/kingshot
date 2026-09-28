import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install_wsa_poc.ps1"
REPORTER = ROOT / "scripts" / "run_and_report.ps1"


class WsaInstallerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = INSTALLER.read_text(encoding="utf-8-sig")

    def test_full_wsa_workflow_runs_elevated(self):
        self.assertNotIn("[switch]$AdminBootstrap", self.source)
        self.assertNotIn("function Invoke-AdminBootstrap", self.source)
        self.assertIn("$childExit = Invoke-SelfElevated", self.source)
        self.assertIn("exit $childExit", self.source)
        self.assertIn('Get-WindowsOptionalFeature -Online -FeatureName "VirtualMachinePlatform"', self.source)

    def test_registration_is_checked_for_current_user(self):
        self.assertIn(
            'Add-AppxPackage -ForceApplicationShutdown -ForceUpdateFromAnyVersion -Register',
            self.source,
        )
        self.assertIn(
            '$installed = Get-AppxPackage | Where-Object',
            self.source,
        )

    def test_adb_probe_is_null_safe_and_bounded(self):
        self.assertIn("$connectResult = Invoke-AdbSafe", self.source)
        self.assertIn("$connectDeadline = (Get-Date).AddMinutes(4)", self.source)
        self.assertIn("if ($null -ne $rawStdout)", self.source)

    def test_report_workflow_defaults_to_wsa_installer(self):
        reporter = REPORTER.read_text(encoding="utf-8-sig")
        self.assertIn('[string]$Backend = "wsa"', reporter)
        self.assertIn('if ($Backend -eq "wsa")', reporter)
        self.assertIn('"install_wsa_poc.ps1"', reporter)


if __name__ == "__main__":
    unittest.main()
