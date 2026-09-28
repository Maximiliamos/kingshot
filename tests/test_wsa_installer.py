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
        self.assertIn('foreach ($featureName in @("VirtualMachinePlatform", "HypervisorPlatform"))', self.source)

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

    def test_p0_pairing_and_port_diagnostics_are_supported(self):
        self.assertIn('[string]$PairEndpoint = ""', self.source)
        self.assertIn('[string]$PairCode = ""', self.source)
        self.assertIn('@("pair", $PairEndpoint, $PairCode)', self.source)
        self.assertIn("excluded-tcp-ranges.txt", self.source)
        self.assertIn("ANDROID_CONTROL_CHANNEL_REFUSED", self.source)

    def test_p0_failure_bundle_collects_runtime_services(self):
        self.assertIn("wsa-logcat-tail.txt", self.source)
        self.assertIn("wsa-connectivity.txt", self.source)
        self.assertIn("wsa-audio.txt", self.source)
        self.assertIn("wsa-processes.txt", self.source)
        self.assertIn("wsa-health.json", self.source)

    def test_archive_hash_does_not_depend_on_get_file_hash_cmdlet(self):
        self.assertIn("function Get-Sha256", self.source)
        self.assertIn("[System.Security.Cryptography.SHA256]::Create()", self.source)
        self.assertNotIn("Get-FileHash", self.source)

    def test_report_workflow_defaults_to_wsa_installer(self):
        reporter = REPORTER.read_text(encoding="utf-8-sig")
        self.assertIn('[string]$Backend = "wsa"', reporter)
        self.assertIn('[string]$Serial = "127.0.0.1:58526"', reporter)
        self.assertIn('[string]$PairEndpoint = ""', reporter)
        self.assertIn('[string]$PairCode = ""', reporter)
        self.assertIn('if ($Backend -eq "wsa")', reporter)
        self.assertIn('"install_wsa_poc.ps1"', reporter)
        self.assertIn('@("-PairEndpoint", $PairEndpoint)', reporter)
        self.assertIn('@("-PairCode", $PairCode)', reporter)


if __name__ == "__main__":
    unittest.main()
