import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "scripts" / "verify_google_services.ps1").read_text(encoding="utf-8")
CLI = (ROOT / "warbot_cli.py").read_text(encoding="utf-8")


class GoogleServicesAcceptanceTests(unittest.TestCase):
    def test_host_gate_uses_dedicated_cli_action_and_persists_evidence(self):
        self.assertIn("google-services-smoke", SCRIPT)
        self.assertIn("google-services.png", SCRIPT)
        self.assertIn("GOOGLE SERVICES HOST GATE PASS", SCRIPT)

    def test_gate_checks_all_required_packages_and_account_privately(self):
        for package in (
            "com.google.android.gms",
            "com.android.vending",
            "com.google.android.gsf",
        ):
            self.assertIn(package, CLI)
        self.assertIn('"account_present": account_present', CLI)
        self.assertNotIn('"account_output": account_output', CLI)
        self.assertIn('"play_store_foreground": foreground', CLI)
        self.assertIn('"ui_frame": frame_ok', CLI)


if __name__ == "__main__":
    unittest.main()
