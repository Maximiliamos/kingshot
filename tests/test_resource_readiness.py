"""Regression coverage for strict resource-readiness policy."""
import unittest
from unittest.mock import MagicMock, patch

import numpy as np

from resource_readiness import evaluate_resource_samples, probe_resource_readiness


class ResourceReadinessTests(unittest.TestCase):
    def good(self):
        return {"frame_valid": True, "known_game_ui": True, "resource_error": False,
                "account_restriction": False, "ocr_available": True}

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

    def test_ocr_absent_cannot_confirm_game_ui(self):
        missing_ocr = {**self.good(), "ocr_available": False}
        result = evaluate_resource_samples(
            [self.good(), missing_ocr], game_process_present=True,
        )
        self.assertFalse(result["pass"])
        self.assertEqual(result["samples_confirmed"], 1)
        # Legacy samples without this proof must also be rejected.
        legacy = {key: value for key, value in self.good().items() if key != "ocr_available"}
        self.assertFalse(evaluate_resource_samples(
            [legacy, legacy], game_process_present=True,
        )["pass"])

    def test_observe_does_not_trust_ui_without_ocr(self):
        from resource_readiness import _observe

        frame = np.zeros((100, 80, 3), dtype=np.uint8)
        frame[:] = [20, 80, 155]
        backend = MagicMock()
        backend.frame.return_value = frame
        with patch("bot.crop_phone", return_value=(frame, 0, 0)), \
             patch("bot.ocr_available", return_value=False), \
             patch("bot.perceive_tutorial_screen") as perception, \
             patch("bot.detect_stop_reason") as stop:
            sample = _observe(backend)
        self.assertTrue(sample["frame_valid"])
        self.assertFalse(sample["known_game_ui"])
        self.assertFalse(sample["ocr_available"])
        self.assertEqual(sample["block_reason"], "ocr_unavailable")
        perception.assert_not_called()
        stop.assert_not_called()

    def test_probe_rejects_missing_ocr_even_with_live_game_process(self):
        backend = MagicMock()
        with patch("resource_readiness.collect_resource_network_diagnostics", return_value={
            "status": "unresolved",
            "signals": {"game_process_present": True}, "probe_errors": {},
        }), patch("resource_readiness._observe", side_effect=[
            self.good(), {**self.good(), "ocr_available": False},
        ]), patch("resource_readiness.time.sleep"):
            report = probe_resource_readiness(backend)
        self.assertFalse(report["pass"])
        self.assertTrue(report["non_destructive"])
        backend.clear_app_data.assert_not_called()

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
