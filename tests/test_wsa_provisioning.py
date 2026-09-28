import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "provision_wsa_for_current_user.ps1"


class WsaProvisioningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SCRIPT.read_text(encoding="utf-8-sig")

    def test_provisions_unpacked_package_for_all_users(self):
        self.assertIn("/Add-ProvisionedAppxPackage", self.source)
        self.assertIn('"/FolderPath:$PackageDir"', self.source)
        self.assertIn("/SkipLicense", self.source)

    def test_registers_provisioned_family_for_interactive_user(self):
        self.assertIn("-RegisterByFamilyName", self.source)
        self.assertIn("$PackageFamily", self.source)
        self.assertIn("WSA_CURRENT_USER_PASS", self.source)

    def test_removal_is_scoped_to_warbot_registration(self):
        self.assertIn('InstallLocation -like "C:\\warbot_wsa\\*"', self.source)
        self.assertIn("Remove-AppxPackage -Package $registration.PackageFullName -AllUsers", self.source)


if __name__ == "__main__":
    unittest.main()
