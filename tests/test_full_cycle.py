import unittest
from unittest.mock import MagicMock, patch

import numpy as np

import bot


class FullCycleTests(unittest.TestCase):
    def test_clean_default_starts_in_initial_tutorial(self):
        self.assertEqual(bot.DEFAULT_STATE["phase"], "tutorial_new_character")
        self.assertEqual(bot.DEFAULT_STATE["step"], "tutorial_intro")
        self.assertEqual(bot.DEFAULT_STATE["tutorial_origin"], "initial")

    def test_initial_tutorial_completion_routes_to_character_creation(self):
        state = dict(bot.DEFAULT_STATE)
        state["tutorial_origin"] = "initial"
        with patch("bot.set_phase", side_effect=lambda s, phase, step: s.update(phase=phase, step=step)):
            bot.finish_tutorial(state)
        self.assertEqual(state["phase"], "create_character")
        self.assertEqual(state["step"], "home")

    def test_new_character_tutorial_completion_routes_to_rename(self):
        state = dict(bot.DEFAULT_STATE)
        state["tutorial_origin"] = "new_character"
        with patch("bot.begin_next_character_cycle") as begin:
            bot.finish_tutorial(state)
        begin.assert_called_once_with(state)

    def test_rename_limit_enters_reset_cycle(self):
        state = dict(bot.DEFAULT_STATE)
        state.update({
            "phase": "rename_governor",
            "step": "rename_verify",
            "pending_nickname": 7,
            "next_nickname": 7,
            "characters_created": 6,
            "characters_created_cycle": 3,
            "characters_per_cycle": 4,
            "auto_reset_data": True,
        })
        phone = np.zeros((944, 421, 3), dtype=np.uint8)
        with patch("bot.match", return_value=None),                 patch("bot.set_phase", side_effect=lambda s, phase, step: s.update(phase=phase, step=step)):
            result = bot.handle_rename_governor(phone, state)

        self.assertFalse(result)
        self.assertEqual(state["next_nickname"], 8)
        self.assertEqual(state["characters_created"], 7)
        self.assertEqual(state["characters_created_cycle"], 4)
        self.assertEqual(state["phase"], "reset_cycle")
        self.assertEqual(state["step"], "clear_data")

    def test_cycle_reset_preserves_pc_nickname_counter(self):
        state = dict(bot.DEFAULT_STATE)
        state.update({
            "next_nickname": 12,
            "pending_nickname": 11,
            "current_cycle": 3,
            "characters_created_cycle": 4,
            "last_stop_reason": "old",
        })
        backend = MagicMock()
        backend.stop_app.return_value = ""
        backend.clear_app_data.return_value = "Success"
        backend.launch_app.return_value = ""

        with patch("bot.get_device_backend", return_value=backend),                 patch("bot.save_state"),                 patch("bot.begin_tutorial", side_effect=lambda s, origin: s.update(
                    phase="tutorial_new_character", step="tutorial_intro", tutorial_origin=origin
                )):
            bot.perform_cycle_reset(state)

        backend.stop_app.assert_called_once()
        backend.clear_app_data.assert_called_once()
        backend.launch_app.assert_called_once()
        self.assertEqual(state["next_nickname"], 12)
        self.assertEqual(state["current_cycle"], 4)
        self.assertEqual(state["characters_created_cycle"], 0)
        self.assertEqual(state["tutorial_origin"], "initial")

    def test_server_limit_is_stop_only(self):
        phone = np.zeros((944, 421, 3), dtype=np.uint8)
        lines = [{"normalized": "достигнутлимитперсонажей"}]
        with patch("bot.ocr_lines", return_value=lines):
            reason = bot.detect_stop_reason(phone)
        self.assertIn("ограничение", reason.lower())


if __name__ == "__main__":
    unittest.main()
