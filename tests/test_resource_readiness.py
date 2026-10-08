"""Regression coverage for strict resource-readiness policy."""
import unittest
from unittest.mock import MagicMock, patch

from resource_readiness import evaluate_resource_samples, probe_resource_readiness


class ResourceReadinessTests(unittest.TestCase):
    def good(self):
        return {"frame_valid": True, "known_game_ui": True, "resource_error": False,
                "account_restriction": False}

    def test_two_game_frames_and_game_pid_are_required(self):
        result = evaluate_resource_samples([self.good(), self.good()], game_process_present=True)
        self.assertTrue(result["pass"])
        self.assertEqual(result["samples_confirmed"], 2)
        self.assertFalse(evaluate_resource_samples([self.good()], game_process_present=True)["pass"])
        self.assertFalse(evaluate_resource_samples([self.good(), self.good()], game_process_present=False)["pass"])

    def test_error_and_restriction_always_fail_closed(self):
        for signal in ("resource_error", "account_restriction"):
            invalid = {**self.good(), signal: True}
            self.assertFalse(evaluate_resource_samples([self.good(), invalid], game_process_present=True)["pass"])
        for signal in ("frame_valid", "known_game_ui"):
            invalid = {**self.good(), signal: False}
            self.assertFalse(evaluate_resource_samples([self.good(), invalid], game_process_present=True)["pass"])

    def test_probe_never_mutates_backend(self):
        backend = MagicMock()
        with patch("resource_readiness.collect_resource_network_diagnostics", return_value={
            "status": "external_resource_loading_unresolved",
            "signals": {"game_process_present": True}, "probe_errors": {},
        }), patch("resource_readiness._observe", side_effect=[self.good(), self.good()]), \
             patch("resource_readiness.time.sleep"):
            report = probe_resource_readiness(backend, run_id="one", head="abc")
        self.assertTrue(report["pass"])
        self.assertTrue(report["non_destructive"])
        self.assertIsNone(report["game_cdn_reachable"])
        self.assertEqual(report["run_id"], "one")
        backend.clear_app_data.assert_not_called()
        backend.stop_app.assert_not_called()
        backend.launch_app.assert_not_called()
        backend.require_ready.assert_called_once()

    def test_capture_error_is_blocked_without_secret_stderr(self):
        backend = MagicMock()
        with patch("resource_readiness.collect_resource_network_diagnostics", return_value={
            "status": "unresolved", "signals": {"game_process_present": True}, "probe_errors": {},
        }), patch("resource_readiness._observe", side_effect=RuntimeError("account password secret")), \
             patch("resource_readiness.time.sleep"):
            report = probe_resource_readiness(backend)
        self.assertFalse(report["pass"])
        self.assertEqual(report["samples"][0]["probe_error"], "RuntimeError")
        self.assertNotIn("password secret", str(report))


if __name__ == "__main__":
    unittest.main()
