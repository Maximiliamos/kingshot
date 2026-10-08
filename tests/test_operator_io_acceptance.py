import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "scripts" / "verify_operator_io.ps1").read_text(encoding="utf-8-sig")


class OperatorIoAcceptanceScriptTests(unittest.TestCase):
    def test_operator_io_gate_is_non_destructive(self):
        self.assertIn("operator-io-smoke", SOURCE)
        self.assertIn("Read-only/non-destructive", SOURCE)
        self.assertIn("OPERATOR I/O HOST GATE PASS", SOURCE)
        self.assertIn("OPERATOR I/O HOST GATE FAIL", SOURCE)


if __name__ == "__main__":
    unittest.main()
