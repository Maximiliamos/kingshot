import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

import bot
import tutorial_vision as tv
from resource_diagnostics import (
    collect_resource_network_diagnostics,
    save_resource_network_diagnostics,
)


class FakeResourceBackend:
    package = "com.got.globalru"

    def __init__(self, *, fail_connectivity=False):
        self.fail_connectivity = fail_connectivity
        self.commands = []

    def shell(self, args, *, timeout=60):
        self.commands.append((list(args), timeout))
        key = " ".join(args)
        if key == "settings get global private_dns_mode":
            return "hostname"
        if key == "settings get global private_dns_specifier":
            return "secret.internal.example"
        if key == "settings get global http_proxy":
            return "private.proxy:3128"
        if key == "dumpsys connectivity":
            if self.fail_connectivity:
                raise TimeoutError("Do not persist hostname or secret")
            return "Capabilities: INTERNET&VALIDATED Transport: TRANSPORT_VPN"
        if key == "pidof com.got.globalru":
            return "3141"
        if key == "logcat -d --pid=3141 -t 600":
            return (
                "UnknownHostException https://private-user:secret@some-cdn/asset\n"
                "SSLHandshakeException https://some-cdn/asset?token=secret\n"
                "SocketTimeoutException"
            )
        raise AssertionError(key)


class ResourceFailureTests(unittest.TestCase):
    def test_game_resource_retry_exhaustion_blocks_without_click(self):
        frame = np.zeros((944, 421, 3), dtype=np.uint8)
        box = tv.Box(216, 573, 128, 59)
        action = tv.Button(
            box, "cyan", True, 0.98, "Повторить попытку", "resource_load_retry"
        )
        screen = tv.ScreenModel(
            buttons=[action],
            panel=tv.Panel("resource_error", 0.93, ("resource-error-ocr",)),
        )
        state = dict(bot.DEFAULT_STATE)
        state["step"] = "tutorial_wait_hand_result"
        state["tutorial_action_lock"] = {
            "signature": tv.BoundedActionPolicy.signature("resource_load_retry", box, frame.shape),
            "acted_at": 1.0,
            "attempts": 2,
        }
        state["resource_retry_attempts"] = 2
        state["resource_retry_last_at"] = 1.0
        with patch("bot.perceive_tutorial_screen", return_value=screen), \
             patch("bot.ocr_available", return_value=True), \
             patch("bot.match", return_value=None), \
             patch("bot.match_tutorial_skip", return_value=None), \
             patch("bot.save_state") as save, \
             patch("bot.log"), \
             patch("bot.tap_match") as tap:
            result = bot.handle_tutorial(frame, state)
        self.assertEqual(result, "resource_blocked")
        self.assertIn("GAME_RESOURCE_LOADING_FAILED", state["last_stop_reason"])
        self.assertIn("2", state["last_stop_reason"])
        save.assert_called()
        tap.assert_not_called()

    def test_moved_retry_button_cannot_reset_resource_budget(self):
        frame = np.zeros((944, 421, 3), dtype=np.uint8)
        original = tv.Box(216, 573, 128, 59)
        moved = tv.Box(220, 590, 125, 60)
        button = tv.Button(moved, "cyan", True, 0.98, "Повторить попытку", "resource_load_retry")
        screen = tv.ScreenModel(
            buttons=[button],
            panel=tv.Panel("resource_error", 0.93, ("resource-error-ocr",)),
        )
        state = dict(bot.DEFAULT_STATE)
        state["step"] = "tutorial_wait_hand_result"
        state["resource_retry_attempts"] = 2
        state["resource_retry_last_at"] = 1.0
        state["tutorial_action_lock"] = {
            "signature": tv.BoundedActionPolicy.signature("resource_load_retry", original, frame.shape),
            "acted_at": 1.0,
            "attempts": 2,
        }
        with patch("bot.perceive_tutorial_screen", return_value=screen), \
             patch("bot.ocr_available", return_value=True), \
             patch("bot.match", return_value=None), \
             patch("bot.match_tutorial_skip", return_value=None), \
             patch("bot.save_state"), patch("bot.log"), \
             patch("bot.tap_match") as tap:
            result = bot.handle_tutorial(frame, state)
        self.assertEqual(result, "resource_blocked")
        self.assertEqual(state["resource_retry_attempts"], 2)
        tap.assert_not_called()

    def test_read_only_diagnostics_are_sanitized(self):
        backend = FakeResourceBackend()
        report = collect_resource_network_diagnostics(
            backend, run_id="test-run", head="abc", phase="tutorial_new_character",
            step="tutorial_wait_hand_result", attempts=2,
        )
        self.assertEqual(report["status"], "external_resource_loading_unresolved")
        self.assertIsNone(report["game_cdn_reachable"])
        self.assertEqual(report["confirmed_retries"], 2)
        self.assertEqual(report["signals"]["private_dns_mode"], "hostname")
        self.assertTrue(report["signals"]["private_dns_hostname_configured"])
        self.assertTrue(report["signals"]["android_proxy_configured"])
        self.assertTrue(report["signals"]["validated_capability_mentioned"])
        self.assertTrue(report["signals"]["vpn_transport_mentioned"])
        self.assertEqual(report["signals"]["game_logcat_error_counts"]["dns_lookup"], 1)
        serialized = json.dumps(report, ensure_ascii=False)
        for secret in ("private.proxy", "secret.internal", "some-cdn", "token="):
            self.assertNotIn(secret, serialized)
        self.assertEqual(len(backend.commands), 6)
        self.assertTrue(all(timeout <= 10 for _, timeout in backend.commands))

    def test_probe_errors_do_not_leak_arbitrary_command_stderr(self):
        report = collect_resource_network_diagnostics(FakeResourceBackend(fail_connectivity=True))
        self.assertEqual(report["probe_errors"]["connectivity"], "TimeoutError")
        self.assertNotIn("Do not persist hostname", json.dumps(report))

    def test_read_only_cli_action_is_available(self):
        import warbot_cli
        args = warbot_cli.build_parser().parse_args(["resource-diagnostics"])
        self.assertEqual(args.action, "resource-diagnostics")

    def test_persisted_diagnostic_is_atomic_json(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "resource-network.json"
            save_resource_network_diagnostics(path, {"schema": 1, "status": "unresolved"})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["schema"], 1)
            self.assertFalse(path.with_name(path.name + ".tmp").exists())


if __name__ == "__main__":
    unittest.main()
