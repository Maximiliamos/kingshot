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

    def test_state3_creation_requires_explicit_confirm_template(self):
        state = dict(bot.DEFAULT_STATE)
        state.update({"phase": "create_character", "step": "state_confirm"})
        phone = np.zeros((944, 421, 3), dtype=np.uint8)
        modal = {"loc": (10, 10), "w": 100, "h": 100, "score": 0.99}
        confirm = {"loc": (100, 500), "w": 80, "h": 30, "score": 0.99}
        with patch("bot.match", side_effect=[modal, confirm]), \
                patch("bot.debug"), \
                patch("bot.tap_match") as tap_match, \
                patch("bot.begin_tutorial") as begin:
            result = bot.handle_create_step(phone, state)

        self.assertTrue(result)
        tap_match.assert_called_once_with(phone, confirm)
        begin.assert_not_called()
        self.assertEqual(state["step"], "state_confirm_applied")

    def test_state3_is_committed_only_after_tutorial_postcondition(self):
        state = dict(bot.DEFAULT_STATE)
        state.update({"phase": "create_character", "step": "state_confirm_applied"})
        phone = np.zeros((944, 421, 3), dtype=np.uint8)
        tutorial = {"loc": (10, 10), "w": 40, "h": 20, "score": 0.99}
        with patch("bot.match", side_effect=[None, tutorial]), \
                patch("bot.emit_event") as emit, \
                patch("bot.begin_tutorial") as begin:
            result = bot.handle_create_step(phone, state)
        self.assertTrue(result)
        emit.assert_called_once()
        self.assertEqual(emit.call_args.args[0], "state3_confirmed")
        self.assertEqual(
            emit.call_args.kwargs["postcondition"],
            "new_character_tutorial_visible",
        )
        begin.assert_called_once_with(state, "new_character")

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
        ocr = [{"text": "Тугарин7", "normalized": "тугарин7", "loc": (0, 0), "w": 20, "h": 10, "score": 99}]
        with patch("bot.match", return_value=None), \
                patch("bot.ocr_lines", return_value=ocr), \
                patch("bot.set_phase", side_effect=lambda s, phase, step: s.update(phase=phase, step=step)):
            result = bot.handle_rename_governor(phone, state)

        self.assertFalse(result)
        self.assertEqual(state["next_nickname"], 8)
        self.assertEqual(state["characters_created"], 7)
        self.assertEqual(state["characters_created_cycle"], 4)
        self.assertEqual(state["phase"], "reset_cycle")
        self.assertEqual(state["step"], "clear_data")

    def test_cycle_can_stop_without_reset_when_repeat_disabled(self):
        state = dict(bot.DEFAULT_STATE)
        state.update({
            "phase": "rename_governor",
            "step": "rename_verify",
            "pending_nickname": 4,
            "next_nickname": 4,
            "characters_created": 3,
            "characters_created_cycle": 3,
            "characters_per_cycle": 4,
            "auto_reset_data": True,
            "repeat_cycles": False,
        })
        phone = np.zeros((944, 421, 3), dtype=np.uint8)
        ocr = [{"text": "Тугарин4", "normalized": "тугарин4", "loc": (0, 0), "w": 20, "h": 10, "score": 99}]
        with patch("bot.match", return_value=None), \
                patch("bot.ocr_lines", return_value=ocr), \
                patch("bot.set_phase", side_effect=lambda s, phase, step: s.update(phase=phase, step=step)):
            bot.handle_rename_governor(phone, state)
        self.assertEqual(state["phase"], "complete")
        self.assertEqual(state["step"], "done")

    def test_rename_does_not_commit_when_exact_name_is_not_visible(self):
        state = dict(bot.DEFAULT_STATE)
        state.update({
            "phase": "rename_governor", "step": "rename_verify",
            "pending_nickname": 9, "next_nickname": 9,
            "characters_created": 8,
        })
        phone = np.zeros((944, 421, 3), dtype=np.uint8)
        wrong = [{"text": "Игрок", "normalized": "игрок", "loc": (0, 0), "w": 20, "h": 10, "score": 99}]
        with patch("bot.match", return_value=None), \
                patch("bot.ocr_lines", return_value=wrong), \
                patch("bot.emit_event") as emit:
            result = bot.handle_rename_governor(phone, state)
        self.assertFalse(result)
        self.assertEqual(state["next_nickname"], 9)
        self.assertEqual(state["characters_created"], 8)
        self.assertFalse(any(
            call.args and call.args[0] == "nickname_committed"
            for call in emit.call_args_list
        ))

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
