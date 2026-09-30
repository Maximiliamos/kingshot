import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "install_tugarin_bots.ps1"
WRAPPER = ROOT / "install_tugarin_bots.cmd"


class ProductInstallerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = INSTALLER.read_text(encoding="utf-8-sig")
        cls.wrapper = WRAPPER.read_text(encoding="utf-8-sig")

    def test_installer_routes_to_dedicated_wsa_setup(self):
        self.assertIn("setup_dedicated_wsa_user.ps1", self.source)
        self.assertIn('"-Branch", $Branch', self.source)
        self.assertIn('Python 3.12+', self.source)
        self.assertIn('Windows Subsystem for Android (WSA)', self.source)

    def test_cmd_wrapper_is_one_click_entrypoint(self):
        self.assertIn("install_tugarin_bots.ps1", self.wrapper)
        self.assertIn("ExecutionPolicy Bypass", self.wrapper)


if __name__ == "__main__":
    unittest.main()
