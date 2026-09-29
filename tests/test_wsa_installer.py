import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install_wsa_poc.ps1"
REPORTER = ROOT / "scripts" / "run_and_report.ps1"


class WsaInstallerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = INSTALLER.read_text(encoding="utf-8-sig")

    def test_default_wsa_workflow_self_elevates_and_keeps_runtime_in_same_context(self):
        self.assertIn("$childExit = Invoke-SelfElevated", self.source)
        self.assertIn("if (-not $RuntimeOnly -and -not (Test-IsAdmin))", self.source)
        self.assertIn('foreach ($featureName in @("VirtualMachinePlatform", "HypervisorPlatform"))', self.source)

    def test_proven_wsaclient_launch_and_deeplink_sequence_is_used(self):
        self.assertIn("Starting the proven WsaClient wake sequence", self.source)
        self.assertIn("Start-Process -FilePath $client", self.source)
        self.assertIn('-ArgumentList "/launch"', self.source)
        self.assertIn('-ArgumentList "/deeplink"', self.source)
        self.assertIn("-WorkingDirectory $clientWorkDir", self.source)
        self.assertIn('$env:PATH = "$clientWorkDir;$oldPath"', self.source)
        self.assertIn("allowing at least 90s before any recycle", self.source)

    def test_developer_registration_registry_failure_is_nonfatal(self):
        self.assertIn("developer-package-registration.txt", self.source)
        self.assertIn("PowerShell registry fallback", self.source)
        self.assertIn("continuing to authoritative WSA registration check", self.source)
        self.assertNotIn('throw "Failed to enable Windows developer package registration."', self.source)

    def test_report_git_metadata_is_rooted_and_null_safe(self):
        self.assertIn("git -C $Root rev-parse HEAD", self.source)
        self.assertIn('if (-not $commitText) { $commitText = "unknown" }', self.source)

    def test_registration_is_checked_for_current_user(self):
        self.assertIn(
            'Add-AppxPackage -ForceApplicationShutdown -ForceUpdateFromAnyVersion -Register',
            self.source,
        )
        self.assertIn(
            '$installed = Get-AppxPackage | Where-Object',
            self.source,
        )

    def test_p0_accepts_semantic_adb_device_state_without_exit_code_gate(self):
        self.assertIn('if ($stateText -eq "device")', self.source)
        self.assertNotIn('$stateResult.ExitCode -eq 0 -and $stateText -eq "device"', self.source)
        self.assertIn('Android control channel accepted on $candidate', self.source)
        self.assertIn('$exitCode = 124', self.source)
        self.assertIn('$proc.Refresh()', self.source)
        self.assertIn('ExitCode = $exitCode', self.source)

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

    def test_p0_classifies_android_authorization_required(self):
        self.assertIn("ANDROID_AUTHORIZATION_REQUIRED", self.source)
        self.assertIn("authorization_required = $true", self.source)
        self.assertIn("failed to authenticate", self.source)
        self.assertIn("Always allow from this computer", self.source)

    def test_p0_developer_fallback_is_pinned_reversible_and_opt_out(self):
        self.assertIn('[switch]$NoAutoDeveloperModePatch', self.source)
        self.assertIn('2e04da1be0765a8a248ab7006ed5f7eeeed15b76', self.source)
        self.assertIn('019f772c0e46e7eed9aaa0a26ea35bf6ef32093e', self.source)
        self.assertIn('function Get-GitBlobSha1', self.source)
        self.assertIn('settings.dat.backup-', self.source)
        self.assertIn('Developer-mode fallback did not recover the channel; original settings restored.', self.source)

    def test_p0_avoids_windows_shell_activation_of_unregistered_wsa_protocols(self):
        self.assertNotIn('Start-Process explorer.exe "wsa-client://developer-settings"', self.source)
        self.assertNotIn('Start-Process explorer.exe "wsa://com.android.settings"', self.source)

    def test_p0_uses_registered_settings_app_and_direct_wsaclient(self):
        self.assertIn('shell:AppsFolder\\MicrosoftCorporationII.WindowsSubsystemForAndroid_8wekyb3d8bbwe!SettingsApp', self.source)
        self.assertIn('Join-Path $installed.InstallLocation "WsaClient\\WsaClient.exe"', self.source)
        self.assertIn('-WorkingDirectory $clientWorkDir', self.source)
        self.assertIn('$directClientAttempted = $true', self.source)

    def test_p0_stays_on_rootless_nogapps_runtime(self):
        self.assertIn("NoGApps-NoAmazon_Windows_10.7z", self.source)
        self.assertIn("366c344eee70e610e905c7588f661ce028faef8ae55ec9cc6c8dd348ec2cb7c8", self.source)
        self.assertIn("WSA_LTS8_Windows10", self.source)
        self.assertNotIn("preparing safe migration to GApps", self.source)
        self.assertNotIn("userdata-before-gapps-", self.source)

    def test_p0_does_not_recycle_after_real_adb_device_state(self):
        self.assertIn('if ($stateText -eq "device")', self.source)
        self.assertIn('$onlineSerial = $candidate', self.source)
        self.assertIn('break', self.source)
        self.assertIn('Android control channel accepted on $candidate', self.source)

    def test_p0_direct_client_fallback_gets_full_startup_grace(self):
        self.assertIn("$directClientStartedAt = $null", self.source)
        self.assertIn("$directClientStartedAt = Get-Date", self.source)
        self.assertIn("TotalSeconds -ge 90", self.source)
        self.assertIn('$env:PATH = "$clientWorkDir;$oldPath"', self.source)
        self.assertIn("allowing at least 90s before any recycle", self.source)

    def test_p0_reports_persist_outside_temp_across_reboot(self):
        self.assertIn('$ReportsRoot = Join-Path $WorkRoot "reports"', self.source)
        self.assertIn('Join-Path $ReportsRoot ("wsa-p0-"', self.source)
        self.assertIn('$latestLocalPath = Join-Path $ReportsRoot "LATEST-LOCAL.json"', self.source)
        self.assertIn('Persistent local report:', self.source)
        self.assertIn('Select-Object -Skip 20', self.source)

    def test_runtime_report_upload_retries_git_network_operations(self):
        uploader = (ROOT / "scripts" / "upload_runtime_report.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("function Invoke-GitWithRetry", uploader)
        self.assertIn('Operation "fetch reports branch"', uploader)
        self.assertIn('Operation "push report"', uploader)
        self.assertIn("Attempts = 3", uploader)

    def test_p0_post_failure_diagnostics_are_bounded_and_nonfatal(self):
        self.assertIn("function Save-AdbDiagnostic", self.source)
        self.assertIn("[int]$TimeoutSeconds = 20", self.source)
        self.assertIn("$proc.WaitForExit", self.source)
        self.assertIn("ADB command timed out after $TimeoutSeconds seconds.", self.source)
        self.assertNotIn('& $adb -s $Serial logcat -b crash -d -v threadtime 2>&1', self.source)

    def test_p0_preserves_full_python_traceback(self):
        self.assertIn("wsa-bootstrap-stderr.txt", self.source)
        self.assertIn("Start-Process -FilePath $pythonExe", self.source)
        self.assertIn("-RedirectStandardError $bootstrapErr", self.source)
        self.assertIn("$bootstrapExit = [int]$proc.ExitCode", self.source)

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

    def test_report_workflow_preserves_dirty_worktree_before_pull(self):
        reporter = REPORTER.read_text(encoding="utf-8-sig")
        self.assertIn("TUGARIN_BOTS_AUTO_BACKUP_", reporter)
        self.assertIn("git stash push -u -m", reporter)
        self.assertIn("They will NOT be dropped automatically", reporter)

    def test_report_workflow_uses_unified_elevated_wsa_installer(self):
        reporter = REPORTER.read_text(encoding="utf-8-sig")
        self.assertIn('[string]$Backend = "wsa"', reporter)
        self.assertIn('[string]$Serial = "127.0.0.1:58526"', reporter)
        self.assertIn('if ($Backend -eq "wsa")', reporter)
        self.assertIn("WSA P0: unified elevated runtime", reporter)
        self.assertIn('"install_wsa_poc.ps1"', reporter)
        self.assertNotIn('"-PrepareOnly"', reporter)
        self.assertNotIn('"-RuntimeOnly"', reporter)
        self.assertIn('@("-PairEndpoint", $PairEndpoint)', reporter)
        self.assertIn('@("-PairCode", $PairCode)', reporter)


if __name__ == "__main__":
    unittest.main()
