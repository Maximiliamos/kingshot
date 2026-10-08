"""Deterministic Android foreground gating and redaction regressions."""
import json
import unittest
from unittest.mock import MagicMock

from game_foreground import classify_foreground, collect_foreground_evidence


PACKAGE = "com.got.globalru"
GAME_WINDOW = "mCurrentFocus=Window{117 u0 com.got.globalru/com.unity3d.player.MyMainPlayerActivity}"
GAME_ACTIVITY = "topResumedActivity=ActivityRecord{391 u0 com.got.globalru/com.unity3d.player.MyMainPlayerActivity t17}"
STORE_WINDOW = "mCurrentFocus=Window{828 u0 com.android.vending/com.google.android.finsky.activities.MainActivity}"
STORE_ACTIVITY = "topResumedActivity=ActivityRecord{888 u0 com.android.vending/com.google.android.finsky.activities.MainActivity t30}"


class AndroidForegroundTests(unittest.TestCase):
    def test_real_game_focus_confirmed(self):
        report = classify_foreground(GAME_WINDOW, GAME_ACTIVITY, PACKAGE)
        self.assertEqual(report["foreground_state"], "game")
        self.assertTrue(report["foreground_confirmed"])
        self.assertEqual(report["window_focus_kind"], "game")
        self.assertNotIn("com.got", json.dumps(report))

    def test_play_store_overlay_blocks_still_running_game(self):
        report = classify_foreground(STORE_WINDOW, STORE_ACTIVITY, PACKAGE)
        self.assertEqual(report["foreground_state"], "other")
        self.assertFalse(report["foreground_confirmed"])

    def test_play_store_focus_with_stale_game_resumed_is_conflict(self):
        report = classify_foreground(STORE_WINDOW, GAME_ACTIVITY, PACKAGE)
        self.assertEqual(report["foreground_state"], "conflict")
        self.assertFalse(report["foreground_confirmed"])

    def test_activity_only_is_accepted_for_older_android_without_window_marker(self):
        report = classify_foreground("", GAME_ACTIVITY, PACKAGE)
        self.assertEqual(report["foreground_state"], "game")

    def test_system_window_in_foreground_does_not_authorize_game(self):
        statusbar = "mCurrentFocus=Window{123 u0 StatusBar}"
        report = classify_foreground(statusbar, GAME_ACTIVITY, PACKAGE)
        self.assertEqual(report["foreground_state"], "conflict")
        self.assertFalse(report["foreground_confirmed"])

    def test_null_or_ambiguous_focus_fails_closed(self):
        for window, activity in [
            ("mCurrentFocus=null", ""),
            ("", ""),
            ("mCurrentFocus=Window{122 u0 StatusBar}", ""),
        ]:
            with self.subTest(window=window):
                self.assertFalse(
                    classify_foreground(window, activity, PACKAGE)["foreground_confirmed"]
                )

    def test_conflicting_multiple_window_focus_lines_fail_closed(self):
        report = classify_foreground(
            GAME_WINDOW + "\n" + STORE_WINDOW, GAME_ACTIVITY, PACKAGE
        )
        self.assertEqual(report["foreground_state"], "conflict")

    def test_only_read_only_commands_and_redacted_labels(self):
        backend = MagicMock()
        backend.package = PACKAGE
        backend.shell.side_effect = [
            STORE_WINDOW + " token=supersecret",
            GAME_ACTIVITY + " url=https://example.invalid/?key=secret",
        ]
        report = collect_foreground_evidence(backend)
        self.assertEqual(report["foreground_state"], "conflict")
        self.assertEqual(backend.shell.call_count, 2)
        backend.shell.assert_any_call(["dumpsys", "window", "windows"], timeout=8)
        backend.shell.assert_any_call(["dumpsys", "activity", "activities"], timeout=8)
        serialized = json.dumps(report)
        for secret in ("com.android.vending", "com.got.globalru", "supersecret",
                       "example.invalid", "key=secret"):
            self.assertNotIn(secret, serialized)
        backend.tap.assert_not_called()
        backend.launch_app.assert_not_called()
        backend.clear_app_data.assert_not_called()

    def test_probe_errors_are_types_only_and_block(self):
        backend = MagicMock()
        backend.package = PACKAGE
        backend.shell.side_effect = RuntimeError("token supersecret")
        report = collect_foreground_evidence(backend)
        self.assertFalse(report["foreground_confirmed"])
        self.assertEqual(report["foreground_probe_errors"], {
            "window": "RuntimeError", "activity": "RuntimeError"
        })
        self.assertNotIn("supersecret", json.dumps(report))


if __name__ == "__main__":
    unittest.main()
