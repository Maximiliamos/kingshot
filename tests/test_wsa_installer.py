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

    def test_manifest_parsing_reads_complete_xml_documents(self):
        self.assertIn(
            '[xml]$preflightManifest = Get-Content -LiteralPath $packageManifestPath -Raw',
            self.source,
        )
        self.assertIn(
            '[xml]$manifest = Get-Content -LiteralPath $manifestPath -Raw -ErrorAction Stop',
            self.source,
        )
        self.assertIn(
            '[xml]$manifestXml = Get-Content -LiteralPath ".\\AppxManifest.xml" -Raw',
            self.source,
        )

    def test_report_manifest_path_cannot_be_overwritten_by_package_preflight(self):
        self.assertIn('$reportManifestPath = Join-Path $stage "manifest.json"', self.source)
        self.assertIn('Set-Content -Encoding UTF8 $reportManifestPath', self.source)
        self.assertIn('$packageManifestPath = Join-Path $installed.InstallLocation "AppxManifest.xml"', self.source)
        self.assertNotIn('$manifestPath = Join-Path $stage "manifest.json"', self.source)

    def test_aumid_catalog_uses_installed_manifest_before_fallback(self):
        start = self.source.index("function Get-WsaApplicationCatalog")
        end = self.source.index("function Save-WsaUserContextDiagnostics", start)
        catalog = self.source[start:end]
        self.assertIn("AppxManifest.xml", catalog)
        self.assertIn('PackageFamilyName + "!" + $id', catalog)
        self.assertNotIn("Get-AppxPackageManifest", catalog)

    def test_gapps_migration_is_explicit_pinned_and_backed_up(self):
        self.assertIn('[ValidateSet("NoGApps", "GApps")]', self.source)
        self.assertIn('if (-not $AllowMagisk)', self.source)
        self.assertIn('501a3ad48c998e9b1e1d91cfbdfb742f8f46f927e9f09dc9b11c70abbe074458', self.source)
        self.assertIn('$ReplaceExistingWsa', self.source)
        self.assertIn('wsa-flavor-migration-', self.source)

    def test_all_runtime_adb_and_python_probes_are_hidden(self):
        self.assertIn('Start-Process -FilePath $adb -ArgumentList $Arguments -WindowStyle Hidden', self.source)
        self.assertNotIn('& $adb -s $Serial shell getprop', self.source)
        self.assertIn('$value = $probe.Stdout.Trim()', self.source)
        self.assertNotIn('$probe.Output.Trim()', self.source)
        self.assertIn('Start-Process -FilePath $pythonExe -ArgumentList $argLine -Wait -PassThru -WindowStyle Hidden', self.source)
        self.assertIn('tugarin-venv\\Scripts\\python.exe', self.source)
        self.assertNotIn('& python .\\warbot_cli.py', self.source)

    def test_packaged_aumid_activation_is_primary_and_raw_exe_is_not_used(self):
        self.assertIn("IApplicationActivationManager", self.source)
        self.assertIn("45BA127D-10A8-46EA-8AB7-56EA9078943C", self.source)
        self.assertIn("Get-WsaApplicationCatalog", self.source)
        self.assertIn("Invoke-WsaPackagedWake", self.source)
        self.assertIn("Launching WSA through registered AppX AUMIDs", self.source)
        self.assertIn("/launch wsa://com.android.settings", self.source)
        self.assertIn("/deeplink wsa-client://developer-settings", self.source)
        self.assertNotIn("Start-Process -FilePath $client", self.source)

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

    def test_p0_accepts_semantic_wsa_device_without_exit_code_gate(self):
        self.assertIn('$probe.is_wsa', self.source)
        self.assertIn('$probe.boot_completed -eq "1"', self.source)
        self.assertIn('$onlineSerial = $candidate', self.source)
        self.assertNotIn('$stateResult.ExitCode -eq 0 -and $stateText -eq "device"', self.source)
        self.assertIn('$exitCode = 124', self.source)
        self.assertIn('$proc.Refresh()', self.source)
        self.assertIn('ExitCode = $exitCode', self.source)

    def test_adb_probe_is_null_safe_bounded_and_adaptive(self):
        self.assertIn("Invoke-WsaCandidateProbe", self.source)
        self.assertIn("$connectDeadline = $probeStartedAt.AddMinutes(3)", self.source)
        self.assertIn("if ($null -ne $rawStdout)", self.source)
        self.assertIn("$elapsed -ge 15", self.source)
        self.assertIn("$elapsed -ge 25", self.source)
        self.assertIn("$elapsed -ge 45", self.source)
        self.assertIn("$elapsed -ge 120", self.source)

    def test_p0_pairing_and_port_diagnostics_are_supported(self):
        self.assertIn('[string]$PairEndpoint = ""', self.source)
        self.assertIn('[string]$PairCode = ""', self.source)
        self.assertIn('@("pair", $PairEndpoint, $PairCode)', self.source)
        self.assertIn("excluded-tcp-ranges-before.txt", self.source)
        self.assertIn("excluded-tcp-ranges-after.txt", self.source)
        self.assertIn("ANDROID_CONTROL_CHANNEL_REFUSED", self.source)

    def test_p0_classifies_android_authorization_required(self):
        self.assertIn("ANDROID_AUTHORIZATION_REQUIRED", self.source)
        self.assertIn("authorization_required = $true", self.source)
        self.assertIn("failed to authenticate", self.source)
        self.assertIn("Approve the debugging prompt", self.source)

    def test_p0_developer_fallback_is_pinned_reversible_and_opt_out(self):
        self.assertIn('[switch]$NoAutoDeveloperModePatch', self.source)
        self.assertIn('2e04da1be0765a8a248ab7006ed5f7eeeed15b76', self.source)
        self.assertIn('019f772c0e46e7eed9aaa0a26ea35bf6ef32093e', self.source)
        self.assertIn('function Get-GitBlobSha1', self.source)
        self.assertIn('settings.dat.backup-', self.source)
        self.assertIn('original settings were restored.', self.source)

    def test_developer_fallback_stops_wsa_before_reading_locked_settings(self):
        fallback_start = self.source.index('function Enable-DeveloperModeFallback')
        fallback_end = self.source.index('function Get-Sha256', fallback_start)
        fallback = self.source[fallback_start:fallback_end]
        self.assertLess(
            fallback.index('Stop-Process -Name "WsaSettings","WsaClient","WindowsSubsystemForAndroid","WsaService","WSACrashUploader","vmmemWSA"'),
            fallback.index('Copy-Item -LiteralPath $settingsPath -Destination $backupPath -Force'),
        )

    def test_p0_avoids_windows_shell_activation_of_unregistered_wsa_protocols(self):
        self.assertNotIn('Start-Process explorer.exe "wsa-client://developer-settings"', self.source)
        self.assertNotIn('Start-Process explorer.exe "wsa://com.android.settings"', self.source)

    def test_p0_uses_registered_settings_app_and_packaged_wsaclient(self):
        self.assertIn('shell:AppsFolder\\MicrosoftCorporationII.WindowsSubsystemForAndroid_8wekyb3d8bbwe!SettingsApp', self.source)
        self.assertIn('$settingsAumid = $installed.PackageFamilyName + "!SettingsApp"', self.source)
        self.assertIn('$clientAumid = $installed.PackageFamilyName + "!App"', self.source)
        self.assertIn("Invoke-PackagedApplication -Aumid $clientAumid", self.source)
        self.assertIn("raw_wsaclient_launch_attempted = $false", self.source)

    def test_p0_stays_on_rootless_nogapps_runtime(self):
        self.assertIn("NoGApps-NoAmazon_Windows_10.7z", self.source)
        self.assertIn("366c344eee70e610e905c7588f661ce028faef8ae55ec9cc6c8dd348ec2cb7c8", self.source)
        self.assertIn("WSA_LTS8_Windows10", self.source)
        self.assertNotIn("preparing safe migration to GApps", self.source)
        self.assertNotIn("userdata-before-gapps-", self.source)

    def test_p0_does_not_recycle_after_verified_wsa_device(self):
        self.assertIn('if ($probe.is_wsa)', self.source)
        self.assertIn('$probe.boot_completed -eq "1"', self.source)
        self.assertIn('$onlineSerial = $candidate', self.source)
        self.assertIn('if ($onlineSerial) { break }', self.source)

    def test_p0_recycle_is_last_resort_after_two_minutes(self):
        self.assertIn("$packagedActivationRetried = $false", self.source)
        self.assertIn("$elapsed -ge 120", self.source)
        self.assertIn("single allowed WSA recycle", self.source)
        self.assertIn("Invoke-WsaPackagedWake", self.source)
        self.assertNotIn("$directClientStartedAt", self.source)

    def test_p0_validates_windows10_wsapatch_package(self):
        self.assertIn("WSA_WIN10_PATCH_INVALID", self.source)
        self.assertIn("WsaPatch.dll", self.source)
        self.assertIn("icu.dll", self.source)
        self.assertIn("manifest_min_version", self.source)
        self.assertIn("manifest_has_custom_install", self.source)
        self.assertIn("wsa-package-preflight.json", self.source)

    def test_p0_requires_ntfs_and_warns_on_long_install_path(self):
        self.assertIn("WSA_INSTALL_VOLUME_UNSUPPORTED", self.source)
        self.assertIn('FileSystem -ne "NTFS"', self.source)
        self.assertIn("WSABuilds documents long extracted paths", self.source)

    def test_p0_reserves_58526_before_startup(self):
        self.assertIn("function Ensure-WsaAdbPortReservation", self.source)
        self.assertIn("add excludedportrange protocol=tcp startport=$Port numberofports=1", self.source)
        self.assertIn("official WSABuilds 10061 prevention", self.source)
        reserve = self.source.index("Ensure-WsaAdbPortReservation -ReportStage $stage")
        launch = self.source.index("Launching WSA through registered AppX AUMIDs", reserve)
        self.assertLess(reserve, launch)

    def test_p0_repairs_loopback_exemption(self):
        self.assertIn("function Ensure-WsaLoopbackExemption", self.source)
        self.assertIn("CheckNetIsolation.exe LoopbackExempt -a", self.source)
        self.assertIn("loopback-exempt-before.txt", self.source)
        self.assertIn("loopback-exempt-after.txt", self.source)

    def test_wsa_shutdown_includes_crash_uploader_that_locks_settings(self):
        self.assertIn('"WsaService","WSACrashUploader","vmmemWSA"', self.source)
        self.assertNotIn('"WsaService","vmmemWSA"', self.source)

    def test_p0_discovers_hns_mdns_and_guest_5555(self):
        self.assertIn("function Get-WsaEndpointCandidates", self.source)
        self.assertIn('Invoke-AdbSafe -Arguments @("mdns", "services")', self.source)
        self.assertIn("Get-HnsEndpoint", self.source)
        self.assertIn("hnsdiag.exe", self.source)
        self.assertIn('($ip + ":5555")', self.source)
        self.assertIn('($ip + ":58526")', self.source)

    def test_p0_fingerprints_wsa_before_accepting_device(self):
        self.assertIn('"ro.product.model"', self.source)
        self.assertIn('"sys.boot_completed"', self.source)
        self.assertIn('"Subsystem for Android(TM)"', self.source)
        self.assertIn("Ignoring Android endpoint", self.source)

    def test_p0_collects_host_side_runtime_diagnostics(self):
        self.assertIn("function Save-WsaHostDiagnostics", self.source)
        self.assertIn("wsa-host-runtime.json", self.source)
        self.assertIn("wsa-hns-endpoints.json", self.source)
        self.assertIn("wsa-application-events.txt", self.source)
        self.assertIn("wsa-host-logcat-tail.txt", self.source)

    def test_p0_records_user_and_package_registration_context(self):
        self.assertIn("function Save-WsaUserContextDiagnostics", self.source)
        self.assertIn("Get-AppxPackage -AllUsers", self.source)
        self.assertIn("wsa-user-context.json", self.source)
        self.assertIn("wsa-applications.json", self.source)
        self.assertIn("wsa-aumids.json", self.source)

    def test_p0_scopes_windows_crash_events_to_current_run(self):
        self.assertIn("[datetime]$Since", self.source)
        self.assertIn("StartTime = $Since", self.source)
        self.assertIn("Save-WsaHostDiagnostics -ReportStage $stage -Since $p0StartedAt", self.source)

    def test_p0_classifies_failure_by_runtime_layer(self):
        for state in (
            "WSA_CLIENT_CRASHED",
            "WSA_PACKAGE_ACTIVATION_FAILED",
            "WSA_RUNTIME_NOT_STARTED",
            "WSA_ADB_NOT_EXPOSED",
            "ANDROID_CONTROL_CHANNEL_REFUSED",
        ):
            self.assertIn(state, self.source)

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
        self.assertIn("git -C $worktree add -f runtime-reports", uploader)

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
