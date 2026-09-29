import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "setup_dedicated_wsa_user.ps1"


class DedicatedWsaUserSetupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SCRIPT.read_text(encoding="utf-8-sig")

    def test_default_target_is_dedicated_local_user(self):
        self.assertIn('[string]$TargetUser = "TugarinBots"', self.source)
        self.assertIn('New-LocalUser', self.source)
        self.assertIn('PasswordNeverExpires', self.source)
        self.assertIn('S-1-5-32-544', self.source)

    def test_local_user_description_fits_windows_limit(self):
        marker = 'Description = "'
        start = self.source.index(marker) + len(marker)
        end = self.source.index('"', start)
        description = self.source[start:end]
        self.assertLessEqual(len(description), 48)

    def test_existing_wsa_is_backed_up_and_removed_for_all_users(self):
        self.assertIn('profile-backups', self.source)
        self.assertIn('userdata.vhdx', self.source)
        self.assertIn('Get-AppxPackage -AllUsers -Name $PackageName', self.source)
        self.assertIn('Remove-AppxPackage -Package $pkg.PackageFullName -AllUsers', self.source)
        self.assertIn('no WSA package registration remains for any Windows user', self.source)

    def test_old_extracted_runtime_is_moved_aside_for_fresh_extract(self):
        self.assertIn('reinstall-backups', self.source)
        self.assertIn('WSA_LTS8_Windows10', self.source)
        self.assertIn('Move-Item -LiteralPath $root -Destination $dest', self.source)
        self.assertIn('archive cache preserved', self.source)

    def test_continuation_is_bound_to_target_user_logon(self):
        self.assertIn('New-ScheduledTaskTrigger -AtLogOn -User $account', self.source)
        self.assertIn('New-ScheduledTaskPrincipal -UserId $account -LogonType Interactive -RunLevel Highest', self.source)
        self.assertIn('Assert-RunningAsDedicatedUser', self.source)
        self.assertIn('$identity.User.Value -ne $target.SID.Value', self.source)

    def test_acl_bootstrap_does_not_recurse_over_full_wsa_tree(self):
        self.assertIn("Grant-BootstrapAccess", self.source)
        self.assertIn("Do NOT recurse over", self.source)
        self.assertIn("Grant-PathAccess -User $User -Path $WorkRoot", self.source)
        self.assertNotIn("Grant-PathAccess -User $User -Path $WorkRoot -Recursive", self.source)

    def test_continuation_updates_repo_and_uses_dedicated_venv(self):
        self.assertIn('safe.directory', self.source)
        self.assertIn('git -C $RepoRoot pull --ff-only origin $Branch', self.source)
        self.assertIn('tugarin-venv', self.source)
        self.assertIn('pip install --disable-pip-version-check -r', self.source)

    def test_gui_launch_is_fail_closed_on_p0(self):
        p0_check = self.source.index('if ($p0Exit -ne 0)')
        shortcut = self.source.index('$shortcutPath = Create-GuiShortcut', p0_check)
        launch = self.source.index('Start-Process explorer.exe', shortcut)
        self.assertLess(p0_check, shortcut)
        self.assertLess(shortcut, launch)
        self.assertIn('P0_FAILED', self.source)
        self.assertIn('WSA_GAME_PASS', self.source)
        self.assertIn('GUI is intentionally not launched', self.source)

    def test_no_plaintext_password_persistence(self):
        self.assertIn('Read-Host "Password for $TargetUser" -AsSecureString', self.source)
        self.assertNotIn('DefaultPassword', self.source)
        self.assertNotIn('AutoAdminLogon', self.source)
        self.assertNotIn('/RP ', self.source)


if __name__ == "__main__":
    unittest.main()
