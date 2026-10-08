import unittest
from tutorial_vision import Box, Button, Panel, ScreenModel
from task_engine import SemanticTaskEngine, TASK_RULES


class TaskEngineTests(unittest.TestCase):
    def model(self, panel="battle_reward", role="battle_reward_claim", enabled=True, confidence=0.95):
        return ScreenModel(
            buttons=[Button(Box(100, 250, 100, 40), "green", enabled, confidence, "Получить", role)],
            panel=Panel(panel, 0.92),
        )

    def test_declared_task_has_postcondition_and_valid_action(self):
        rule = TASK_RULES["battle_reward_claim"]
        self.assertEqual(rule.action, "tap")
        self.assertEqual(rule.next_step, "tutorial_wait_hand_result")
        self.assertEqual(rule.postcondition, "recognized_role_disappears")

    def test_positive_action_wait_retry_and_exhaustion(self):
        engine = SemanticTaskEngine()
        state = {}
        screen = self.model()
        choices = [engine.plan(state, screen, "battle_reward_claim", (944, 421, 3), now=t).status
                   for t in (10.0, 11.0, 14.0, 18.0)]
        self.assertEqual(choices, ["act", "wait", "retry", "exhausted"])
        self.assertEqual(state["tutorial_action_lock"]["attempts"], 2)

    def test_missing_role_or_wrong_panel_is_not_actionable(self):
        engine = SemanticTaskEngine()
        for scene in ("city_home", "resource_error", "construction"):
            self.assertIsNone(engine.plan({}, self.model(panel=scene), "battle_reward_claim", (944, 421, 3)))
        self.assertIsNone(engine.plan({}, self.model(), "unknown", (944, 421, 3)))
        self.assertIsNone(engine.plan({}, self.model(enabled=False), "battle_reward_claim", (944, 421, 3)))
        self.assertIsNone(engine.plan({}, self.model(confidence=0.5), "battle_reward_claim", (944, 421, 3)))

    def test_out_of_frame_target_never_mutates_state(self):
        engine = SemanticTaskEngine()
        state = {}
        model = ScreenModel(
            buttons=[Button(Box(400, 250, 100, 40), "green", True, 0.99, role="battle_conquer")],
            panel=Panel("battle_reward", 0.9),
        )
        self.assertIsNone(engine.plan(state, model, "battle_conquer", (944, 421, 3)))
        self.assertEqual(state, {})

    def test_high_risk_operations_are_not_generic_tasks(self):
        for forbidden in ("resource_load_retry", "select_state_3", "rename", "account_limit"):
            self.assertNotIn(forbidden, TASK_RULES)

    def test_resident_completion_is_a_bounded_back_action_on_its_exact_panel(self):
        engine = SemanticTaskEngine()
        screen = self.model(panel="resident_assignment", role="resident_complete", enabled=False)
        decision = engine.plan({}, screen, "resident_complete", (944, 421, 3), now=10.0)
        self.assertIsNotNone(decision)
        self.assertEqual(decision.rule.action, "key_back")
        self.assertEqual(decision.rule.postcondition, "resident_assignment_panel_closes")


if __name__ == "__main__":
    unittest.main()
