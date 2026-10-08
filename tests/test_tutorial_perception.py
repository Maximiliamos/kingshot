import inspect
import unittest
from unittest.mock import patch

import cv2
import inspect
import numpy as np

import bot
import tutorial_vision as tv


class TutorialPerceptionTests(unittest.TestCase):
    def test_grey_button_is_not_inherently_disabled(self):
        frame = np.zeros((944, 421, 3), dtype=np.uint8)
        cv2.rectangle(frame, (250, 500), (380, 560), (120, 120, 120), -1)
        buttons = tv.UniversalButtonDetector().detect(frame)
        self.assertTrue(any(button.style == "grey" and button.enabled for button in buttons))

    def test_ocr_text_is_bound_to_containing_button(self):
        button = tv.Button(tv.Box(100, 200, 160, 60), "cyan", True, 0.9)
        lines = [{"text": "Улучшить", "normalized": "улучшить",
                  "loc": (130, 215), "w": 90, "h": 24, "score": 95}]
        bound = tv.TutorialPerception._bind_ocr([button], lines)
        self.assertEqual(bound[0].text, "Улучшить")

    def test_grey_construction_primary_is_contextually_enabled(self):
        frame = np.zeros((944, 421, 3), dtype=np.uint8)
        cv2.rectangle(frame, (0, 380), (420, 943), (170, 190, 205), -1)
        cv2.rectangle(frame, (250, 470), (400, 535), (120, 120, 120), -1)
        lines = [{"text": "Улучшить", "normalized": "улучшить",
                  "loc": (275, 485), "w": 90, "h": 25, "score": 95}]
        model = tv.TutorialPerception().perceive(frame, ocr_lines=lines)
        primary = model.button("construction_upgrade")
        self.assertEqual(model.panel.kind, "construction")
        self.assertIsNotNone(primary)
        self.assertEqual(primary.style, "grey")
        self.assertTrue(primary.enabled)
        self.assertEqual(primary.text, "Улучшить")

    def test_cyan_upgrade_text_gets_hold_semantics(self):
        frame = np.zeros((944, 421, 3), dtype=np.uint8)
        cv2.rectangle(frame, (0, 380), (420, 943), (170, 190, 205), -1)
        cv2.rectangle(frame, (230, 500), (395, 565), (220, 180, 40), -1)
        lines = [{
            "text": "Улучшить", "normalized": "улучшить",
            "loc": (265, 515), "w": 95, "h": 25, "score": 95,
        }]
        model = tv.TutorialPerception().perceive(frame, ocr_lines=lines)
        self.assertIsNotNone(model.button("construction_upgrade"))

    def test_battle_reward_claim_requires_bound_text_and_context(self):
        frame = np.zeros((944, 421, 3), dtype=np.uint8)
        cv2.rectangle(frame, (35, 300), (385, 900), (190, 210, 225), -1)
        cv2.rectangle(frame, (302, 670), (369, 708), (30, 190, 45), -1)
        cv2.rectangle(frame, (139, 843), (282, 899), (220, 180, 40), -1)
        lines = [
            {"text": "Завоевание", "normalized": "завоевание", "loc": (77, 44), "w": 90, "h": 30, "score": 90},
            {"text": "Получить", "normalized": "получить", "loc": (306, 681), "w": 59, "h": 19, "score": 95},
            {"text": "Завоевать", "normalized": "завоевать", "loc": (171, 856), "w": 79, "h": 26, "score": 90},
        ]
        model = tv.TutorialPerception().perceive(frame, ocr_lines=lines)
        claim = model.button("battle_reward_claim")
        conquer = model.button("battle_conquer")
        self.assertEqual(model.panel.kind, "battle_reward")
        self.assertIsNotNone(claim)
        self.assertEqual(claim.text, "Получить")
        self.assertIsNotNone(conquer)

        no_context = tv.TutorialPerception().perceive(frame, ocr_lines=lines[1:])
        self.assertIsNone(no_context.button("battle_reward_claim"))

    def test_battle_reward_colours_without_context_remain_generic(self):
        frame = np.zeros((944, 421, 3), dtype=np.uint8)
        cv2.rectangle(frame, (302, 670), (369, 708), (30, 190, 45), -1)
        model = tv.TutorialPerception().perceive(frame, ocr_lines=[])
        self.assertIsNone(model.button("battle_reward_claim"))
        self.assertIsNone(model.button("battle_conquer"))

    def test_battle_actions_use_bounded_policy(self):
        source = inspect.getsource(bot.handle_tutorial)
        self.assertIn('state, "battle_reward_claim"', source)
        self.assertIn('state, "battle_conquer"', source)
        self.assertGreaterEqual(source.count("TUTORIAL_ACTION_POLICY.decide("), 4)

    def test_resource_retry_requires_error_context_and_bound_text(self):
        frame = np.zeros((944, 421, 3), dtype=np.uint8)
        cv2.rectangle(frame, (55, 330), (365, 650), (190, 210, 225), -1)
        cv2.rectangle(frame, (77, 573), (204, 632), (20, 130, 230), -1)
        cv2.rectangle(frame, (216, 573), (344, 632), (220, 180, 40), -1)
        lines = [
            {"text": "Не удалось загрузить ресурсы", "normalized": "неудалосьзагрузитьресурсы", "loc": (65, 400), "w": 280, "h": 50, "score": 90},
            {"text": "Повторить попытку", "normalized": "повторитьпопытку", "loc": (220, 580), "w": 120, "h": 30, "score": 90},
        ]
        model = tv.TutorialPerception().perceive(frame, ocr_lines=lines)
        self.assertEqual(model.panel.kind, "resource_error")
        self.assertIsNotNone(model.button("resource_load_retry"))
        self.assertIsNone(tv.TutorialPerception().perceive(frame, ocr_lines=lines[1:]).button("resource_load_retry"))

    def test_novel_guidance_requires_temporal_motion(self):
        detector = tv.TutorialGuidanceDetector()
        candidate = {
            "center": (200, 500), "radius": 30, "confidence": 0.90,
            "glow": 0.30, "white": 0.20, "cuff": 0.05,
            "motion": 0.02, "global_motion": 0.01, "temporal_motion": True,
            "evidence": ["target-glow", "pointer-colors", "temporal-motion"],
        }
        frame = np.zeros((944, 421, 3), dtype=np.uint8)
        with patch.object(detector, "_circle_candidates", return_value=[candidate]):
            target, _ = detector.detect(frame)
        self.assertIsNotNone(target)
        self.assertEqual(target.source, "universal-glow-pointer")
        static = dict(candidate, motion=0.0, temporal_motion=False,
                      evidence=["target-glow", "pointer-colors"])
        with patch.object(detector, "_circle_candidates", return_value=[static]):
            target, _ = detector.detect(frame)
        self.assertIsNone(target)

    def test_production_paths_do_not_reintroduce_scene_hand_templates(self):
        tutorial_source = inspect.getsource(bot.handle_tutorial)
        rename_source = inspect.getsource(bot.handle_rename_governor)
        self.assertNotIn("match_tutorial_hand(", tutorial_source)
        self.assertNotIn("match_tutorial_hand(", rename_source)
        self.assertNotIn("tutorial_hand_building.png", tutorial_source)
        self.assertNotIn("upgrade_button.png", tutorial_source)

    def test_action_policy_is_bounded(self):
        policy = tv.BoundedActionPolicy()
        state = {}
        box = tv.Box(100, 200, 30, 30)
        shape = (944, 421, 3)
        self.assertEqual(policy.decide(state, "tutorial_target", box, shape, now=0), "act")
        self.assertEqual(policy.decide(state, "tutorial_target", box, shape, now=1), "wait")
        self.assertEqual(policy.decide(state, "tutorial_target", box, shape, now=4), "retry")
        self.assertEqual(policy.decide(state, "tutorial_target", box, shape, now=8), "exhausted")


if __name__ == "__main__":
    unittest.main()
