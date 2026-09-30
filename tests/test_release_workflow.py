import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUDIT = (ROOT / "scripts" / "audit_runtime_processes.ps1").read_text(encoding="utf-8-sig")
RELEASE = (ROOT / "scripts" / "verify_release.ps1").read_text(encoding="utf-8-sig")
MVP = (ROOT / "scripts" / "verify_mvp.ps1").read_text(encoding="utf-8-sig")
INSTALL = (ROOT / "scripts" / "install.ps1").read_text(encoding="utf-8-sig")


class ReleaseWorkflowTests(unittest.TestCase):
    def test_process_audit_records_adb_but_only_fails_stale_console_python(self):
        self.assertIn('"python", "pythonw", "cmd", "powershell", "pwsh", "conhost", "adb"', AUDIT)
        self.assertIn('"python", "pythonw", "cmd", "conhost"', AUDIT)
        self.assertNotIn('"python", "pythonw", "cmd", "powershell", "pwsh", "conhost"\n        )', AUDIT)
        self.assertIn("stale_processes", AUDIT)
        self.assertIn("exit 21", AUDIT)

    def test_release_gate_runs_tests_mvp_and_process_audit(self):
        self.assertIn("unittest discover -s tests -v", RELEASE)
        self.assertIn("verify_mvp.ps1", RELEASE)
        self.assertIn("audit_runtime_processes.ps1", RELEASE)
        self.assertIn("RELEASE HOST GATE PASS", RELEASE)

    def test_release_gate_prefers_dedicated_python_and_syncs_dependencies(self):
        self.assertIn(r"C:\\warbot_wsa\\tugarin-venv\\Scripts\\python.exe", RELEASE)
        self.assertIn(r"C:\\warbot_wsa\\release-venv", RELEASE)
        self.assertIn("-m venv $releaseVenvRoot", RELEASE)
        self.assertIn("requirements.txt", RELEASE)
        self.assertIn("Runtime imports: OK", RELEASE)
        self.assertIn("& $ReleasePython -m unittest", RELEASE)
        self.assertIn('"-PythonExe", $ReleasePython', RELEASE)
        self.assertNotIn("& python -m unittest", RELEASE)

    def test_mvp_gate_uses_the_release_selected_python(self):
        self.assertIn('[string]$PythonExe = ""', MVP)
        self.assertIn("& $PythonExe @args", MVP)
        self.assertIn("& $PythonExe .\\warbot_cli.py status", MVP)
        self.assertNotIn("& python @args", MVP)

    def test_copy_installer_ships_hardening_modules(self):
        for name in (
            "frame_stream.py",
            "runtime_events.py",
            "runtime_recovery.py",
            "runtime_watchdog.py",
            "run_gui.vbs",
        ):
            self.assertIn(name, INSTALL)


if __name__ == "__main__":
    unittest.main()
