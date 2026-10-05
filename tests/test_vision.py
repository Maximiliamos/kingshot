import unittest
from unittest.mock import patch

import cv2
import numpy as np

import bot


class TutorialVisionTests(unittest.TestCase):
    def test_action_gate_supports_bounded_local_change_threshold(self):
        before = np.zeros((100, 100, 3), dtype=np.uint8)
        after = before.copy()
        after[40:60, 40:60] = 20
        gate = bot.ActionGate()
        gate.arm(before, "resident assignment", change_threshold=0.5)
        gate.started_at -= bot.ACTION_MIN_SETTLE + 0.1
        self.assertEqual(gate.observe(after), "changed")

        strict_gate = bot.ActionGate()
        strict_gate.arm(before, "scene transition")
        strict_gate.started_at -= bot.ACTION_MIN_SETTLE + 0.1
        self.assertEqual(strict_gate.observe(after), "waiting")

    def test_offline_confirm_accepts_wsa_anisotropic_scaling_in_safe_zone(self):
        frame = np.zeros((944, 421, 3), dtype=np.uint8)
        template = bot.tpl("offline_confirm.png")
        stretched = cv2.resize(template, None, fx=0.75, fy=1.10)
        height, width = stretched.shape[:2]
        x, y = (frame.shape[1] - width) // 2, 742
        frame[y:y + height, x:x + width] = stretched
        hit = bot.match_offline_confirm(frame)
        self.assertIsNotNone(hit)
        self.assertGreaterEqual(hit["score"], 0.80)

    def test_offline_confirm_rejects_same_button_outside_modal_action_zone(self):
        frame = np.zeros((944, 421, 3), dtype=np.uint8)
        template = bot.tpl("offline_confirm.png")
        stretched = cv2.resize(template, None, fx=0.75, fy=1.10)
        height, width = stretched.shape[:2]
        x, y = (frame.shape[1] - width) // 2, 120
        frame[y:y + height, x:x + width] = stretched
        self.assertIsNone(bot.match_offline_confirm(frame))

    def test_resident_assignment_plus_requires_beige_panel_and_safe_position(self):
        frame = np.zeros((944, 421, 3), dtype=np.uint8)
        cv2.rectangle(frame, (35, 360), (386, 910), (190, 210, 225), -1)
        cv2.rectangle(frame, (250, 760), (282, 814), (30, 190, 45), -1)
        hit = bot.find_resident_assignment_plus(frame)
        self.assertIsNotNone(hit)
        self.assertGreaterEqual(hit["loc"][1], 760)

        no_panel = np.zeros_like(frame)
        cv2.rectangle(no_panel, (250, 760), (282, 814), (30, 190, 45), -1)
        self.assertIsNone(bot.find_resident_assignment_plus(no_panel))

    def test_current_loading_splash_is_detected_without_clicking(self):
        frame = np.zeros((944, 421, 3), dtype=np.uint8)
        cv2.rectangle(frame, (135, 190), (260, 250), (0, 145, 255), -1)
        cv2.rectangle(frame, (140, 846), (250, 872), (0, 165, 255), -1)
        self.assertTrue(bot.loading_screen_visible(frame))

    def test_plain_scene_is_not_misclassified_as_loading(self):
        frame = np.full((944, 421, 3), 80, dtype=np.uint8)
        self.assertFalse(bot.loading_screen_visible(frame))

    def test_newbie_offer_requires_close_in_safe_zone_and_yellow_panel(self):
        frame = np.zeros((944, 421, 3), dtype=np.uint8)
        cv2.rectangle(frame, (60, 240), (360, 775), (0, 190, 255), -1)
        close = {"score": 0.78, "loc": (307, 61), "w": 52, "h": 48}
        with patch("bot.match", return_value=close):
            self.assertEqual(bot.match_newbie_offer_close(frame), close)

        close_left = dict(close, loc=(30, 61))
        with patch("bot.match", return_value=close_left):
            self.assertIsNone(bot.match_newbie_offer_close(frame))

        no_panel = np.zeros_like(frame)
        with patch("bot.match", return_value=close):
            self.assertIsNone(bot.match_newbie_offer_close(no_panel))

    def test_direct_android_frame_sets_real_input_geometry(self):
        frame = np.zeros((2376, 1060, 3), dtype=np.uint8)
        old = (
            bot.INPUT_W, bot.INPUT_H,
            bot.INPUT_CONTENT_LEFT, bot.INPUT_CONTENT_TOP,
            bot.INPUT_CONTENT_W, bot.INPUT_CONTENT_H,
        )
        try:
            with patch.object(bot, "BACKEND_NAME", "native_arm64"):
                bot.sync_input_geometry(
                    frame,
                    {"left": 0, "top": 0, "width": 1060, "height": 2376},
                )
            self.assertEqual((bot.INPUT_W, bot.INPUT_H), (1060, 2376))
            with patch("bot.tap") as tap:
                bot.tap_norm(0.5, 0.25)
            tap.assert_called_once_with(530.0, 594.0)
        finally:
            (
                bot.INPUT_W, bot.INPUT_H,
                bot.INPUT_CONTENT_LEFT, bot.INPUT_CONTENT_TOP,
                bot.INPUT_CONTENT_W, bot.INPUT_CONTENT_H,
            ) = old

    def test_wsa_letterbox_maps_phone_coordinates_into_center_content(self):
        # The H.264 preview is 1280x720 but input targets the physical
        # 1920x1080 WSA framebuffer. Kingshot is portrait in the center.
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        old = (
            bot.INPUT_W, bot.INPUT_H,
            bot.INPUT_CONTENT_LEFT, bot.INPUT_CONTENT_TOP,
            bot.INPUT_CONTENT_W, bot.INPUT_CONTENT_H,
        )
        try:
            with patch.object(bot, "BACKEND_NAME", "wsa"):
                bot.sync_input_geometry(
                    frame,
                    {"left": 0, "top": 0, "width": 1920, "height": 1080},
                )
            self.assertEqual((bot.INPUT_W, bot.INPUT_H), (1920, 1080))
            self.assertAlmostEqual(bot.INPUT_CONTENT_H, 1080.0, places=3)
            self.assertAlmostEqual(
                bot.INPUT_CONTENT_W,
                1080.0 * bot.PHONE_W / bot.PHONE_H,
                delta=1.0,
            )
            with patch("bot.tap") as tap:
                bot.tap_norm(0.5, 0.5)
            x, y = tap.call_args.args
            self.assertAlmostEqual(x, 960.0, delta=1.0)
            self.assertAlmostEqual(y, 540.0, places=3)

            with patch("bot.tap") as tap:
                bot.tap_norm(0.0, 0.5)
            x, y = tap.call_args.args
            self.assertGreater(x, 700.0)
            self.assertLess(x, 740.0)
            self.assertAlmostEqual(y, 540.0, places=3)
        finally:
            (
                bot.INPUT_W, bot.INPUT_H,
                bot.INPUT_CONTENT_LEFT, bot.INPUT_CONTENT_TOP,
                bot.INPUT_CONTENT_W, bot.INPUT_CONTENT_H,
            ) = old

    def test_crop_phone_normalizes_any_scrcpy_window_size(self):
        frame = np.zeros((1416, 632, 3), dtype=np.uint8)
        phone, _, _ = bot.crop_phone(frame)
        self.assertEqual(phone.shape[:2], (bot.VISION_H, bot.VISION_W))

    def test_crop_phone_removes_horizontal_letterbox(self):
        frame = np.zeros((944, 700, 3), dtype=np.uint8)
        frame[:, 139:560] = 255
        phone, _, _ = bot.crop_phone(frame)
        self.assertEqual(phone.shape[:2], (bot.VISION_H, bot.VISION_W))
        self.assertGreater(float(phone.mean()), 250.0)

    def test_crop_phone_removes_vertical_letterbox(self):
        frame = np.zeros((1200, 421, 3), dtype=np.uint8)
        frame[128:1072, :] = 255
        phone, _, _ = bot.crop_phone(frame)
        self.assertEqual(phone.shape[:2], (bot.VISION_H, bot.VISION_W))
        self.assertGreater(float(phone.mean()), 250.0)

    def test_wsa_crop_uses_actual_nonblack_viewport_not_phone_aspect_guess(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        frame[155:884, 677:1173] = (40, 80, 120)

        rect = bot.detect_content_rect(frame)
        phone, left, right = bot.crop_phone(frame)

        self.assertEqual(rect, (677, 155, 496, 729))
        self.assertEqual((left, right), (677, 1173))
        self.assertEqual(phone.shape[:2], (bot.VISION_H, bot.VISION_W))
        self.assertGreater(float(phone.mean()), 70.0)

    def test_skip_is_found_in_common_upper_right_area(self):
        phone = np.zeros((944, 421, 3), dtype=np.uint8)
        template = bot.tpl("tutorial_skip.png")
        height, width = template.shape[:2]
        phone[74:74 + height, 270:270 + width] = template

        hit = bot.match_tutorial_skip(phone)

        self.assertIsNotNone(hit)
        self.assertGreaterEqual(hit["score"], 0.99)
        self.assertEqual(hit["loc"], (270, 74))

    def test_skip_does_not_match_outside_its_dialog_area(self):
        phone = np.zeros((944, 421, 3), dtype=np.uint8)
        template = bot.tpl("tutorial_skip.png")
        height, width = template.shape[:2]
        phone[700:700 + height, 270:270 + width] = template

        self.assertIsNone(bot.match_tutorial_skip(phone))

    def test_skip_is_found_when_scrcpy_is_smaller(self):
        phone = np.zeros((944, 421, 3), dtype=np.uint8)
        template = bot.tpl("tutorial_skip.png")
        small = cv2.resize(template, None, fx=0.8, fy=0.8, interpolation=cv2.INTER_AREA)
        height, width = small.shape[:2]
        phone[74:74 + height, 270:270 + width] = small

        hit = bot.match_tutorial_skip(phone)

        self.assertIsNotNone(hit)
        self.assertEqual(hit["loc"], (270, 74))

    def test_compact_skip_variant_is_found(self):
        phone = np.zeros((944, 421, 3), dtype=np.uint8)
        template = bot.tpl("tutorial_skip_small.png")
        height, width = template.shape[:2]
        phone[65:65 + height, 231:231 + width] = template

        hit = bot.match_tutorial_skip(phone)

        self.assertIsNotNone(hit)
        self.assertEqual(hit["loc"], (231, 65))

    def test_core_skip_variant_is_found(self):
        phone = np.zeros((944, 421, 3), dtype=np.uint8)
        template = bot.tpl("tutorial_skip_core.png")
        height, width = template.shape[:2]
        phone[69:69 + height, 235:235 + width] = template

        hit = bot.match_tutorial_skip(phone)

        self.assertIsNotNone(hit)
        self.assertEqual(hit["loc"], (235, 69))

    def test_action_gate_reports_a_confirmed_frame_change(self):
        gate = bot.ActionGate()
        before = np.zeros((10, 10, 3), dtype=np.uint8)
        after = np.full((10, 10, 3), 255, dtype=np.uint8)
        gate.arm(before, "skip")
        gate.started_at -= 1.0

        self.assertEqual(gate.observe(after), "changed")

    def test_ocr_continue_requires_explicit_lower_dialogue_text(self):
        phone = np.zeros((944, 421, 3), dtype=np.uint8)
        safe = {"action": "continue", "loc": (140, 700), "w": 120, "h": 30}
        unsafe = {"action": "continue", "loc": (140, 100), "w": 120, "h": 30}
        self.assertTrue(bot.ocr_action_is_safe(phone, safe))
        self.assertFalse(bot.ocr_action_is_safe(phone, unsafe))

    def test_ocr_upgrade_outside_construction_area_is_rejected(self):
        phone = np.zeros((807, 360, 3), dtype=np.uint8)
        target = {"action": "upgrade", "loc": (180, 775), "w": 120, "h": 25}

        self.assertFalse(bot.ocr_action_is_safe(phone, target))

    def test_tower_build_button_is_found(self):
        phone = np.zeros((944, 421, 3), dtype=np.uint8)
        template = bot.tpl("tutorial_build_tower_button.png")
        height, width = template.shape[:2]
        phone[577:577 + height, 110:110 + width] = template

        self.assertIsNotNone(bot.match(phone, template, 0.94))

    def test_battle_pause_marker_is_recognized(self):
        phone = np.zeros((944, 421, 3), dtype=np.uint8)
        template = bot.tpl("tutorial_battle_pause.png")
        height, width = template.shape[:2]
        phone[870:870 + height, 39:39 + width] = template
        self.assertIsNotNone(bot.match(phone, template, 0.90))

    def test_compact_return_city_button_is_found(self):
        phone = np.zeros((944, 421, 3), dtype=np.uint8)
        template = bot.tpl("tutorial_return_city_small.png")
        height, width = template.shape[:2]
        phone[535:535 + height, 106:106 + width] = template

        self.assertIsNotNone(bot.match(phone, template, 0.94))

    def test_match_adapts_to_resized_scrcpy_window(self):
        template = bot.tpl("tutorial_cook_button.png")
        scaled = cv2.resize(template, None, fx=1.18, fy=1.18, interpolation=cv2.INTER_CUBIC)
        phone = np.zeros((944, 421, 3), dtype=np.uint8)
        height, width = scaled.shape[:2]
        phone[520:520 + height, 140:140 + width] = scaled

        hit = bot.match(phone, template, 0.90)

        self.assertIsNotNone(hit)
        self.assertEqual(hit["loc"], (140, 520))

    def test_text_normalization_preserves_cyrillic(self):
        self.assertEqual(bot.norm_text("Вернуться в город!"), "вернутьсявгород")

    def test_nickname_number_is_typed_digit_by_digit(self):
        phone = np.zeros((944, 421, 3), dtype=np.uint8)
        with patch("bot.tap_client") as tap_client:
            bot.type_tugarin_on_russian_keyboard(phone, 23)
        numeric_taps = [call.args[1:] for call in tap_client.call_args_list[-2:]]
        self.assertEqual(numeric_taps, [(0.154, 0.781), (0.254, 0.781)])

    def test_nickname_has_no_inserted_space_before_number(self):
        phone = np.zeros((944, 421, 3), dtype=np.uint8)
        with patch("bot.tap_client") as tap_client:
            bot.type_tugarin_on_russian_keyboard(phone, 1)
        taps = [call.args[1:] for call in tap_client.call_args_list]
        self.assertNotIn((0.50, 0.953), taps)
        self.assertEqual(taps[-2:], [(0.06, 0.953), (0.055, 0.781)])

    def test_shared_tutorial_hand_is_found(self):
        phone = np.zeros((944, 421, 3), dtype=np.uint8)
        template = bot.tpl("tutorial_hand_target.png")
        height, width = template.shape[:2]
        phone[390:390 + height, 188:188 + width] = template

        hit = bot.match_tutorial_hand(phone)

        self.assertIsNotNone(hit)
        self.assertGreaterEqual(hit["score"], 0.99)
        self.assertEqual(hit["loc"], (188, 390))

    def test_animated_first_building_hand_is_found_at_safe_score(self):
        # Runtime unknown/ is intentionally gitignored. CI must still exercise
        # the tutorial-hand gate instead of silently skipping the regression.
        phone = np.zeros((944, 421, 3), dtype=np.uint8)
        template = bot.tpl("tutorial_hand_building.png")
        height, width = template.shape[:2]
        x, y = 126, 352
        phone[y:y + height, x:x + width] = template
        # The production matcher requires both the hand and a lit target.
        target_x = round(x + width * 0.25)
        target_y = round(y + height * 0.93)
        radius = max(8, round(min(width, height) * 0.11))
        cv2.circle(phone, (target_x, target_y), radius, (0, 255, 255), -1)

        hit = bot.match_tutorial_hand(phone)

        self.assertIsNotNone(hit)
        self.assertEqual(hit["variant"], "tutorial_hand_building.png")
        self.assertGreaterEqual(hit["score"], 0.68)
        self.assertLessEqual(abs(hit["loc"][0] - x), 2)
        self.assertLessEqual(abs(hit["loc"][1] - y), 2)

    def test_roof_hand_variant_is_found(self):
        phone = np.zeros((944, 421, 3), dtype=np.uint8)
        template = bot.tpl("tutorial_hand_roof.png")
        height, width = template.shape[:2]
        phone[392:392 + height, 159:159 + width] = template

        hit = bot.match_tutorial_hand(phone)

        self.assertIsNotNone(hit)
        self.assertEqual(hit["variant"], "tutorial_hand_roof.png")
        self.assertEqual(hit["loc"], (159, 392))

    def test_housing_hand_variant_is_found(self):
        phone = np.zeros((944, 421, 3), dtype=np.uint8)
        template = bot.tpl("tutorial_hand_housing.png")
        height, width = template.shape[:2]
        phone[685:685 + height, 190:190 + width] = template

        hit = bot.match_tutorial_hand(phone)

        self.assertIsNotNone(hit)
        self.assertEqual(hit["variant"], "tutorial_hand_housing.png")
        self.assertEqual(hit["loc"], (190, 685))

    def test_residents_hand_variant_is_found(self):
        phone = np.zeros((944, 421, 3), dtype=np.uint8)
        template = bot.tpl("tutorial_hand_residents.png")
        height, width = template.shape[:2]
        phone[615:615 + height, 195:195 + width] = template

        hit = bot.match_tutorial_hand(phone)

        self.assertIsNotNone(hit)
        self.assertEqual(hit["variant"], "tutorial_hand_residents.png")
        self.assertEqual(hit["loc"], (195, 615))

    def test_hand_false_match_without_lit_target_is_rejected(self):
        phone = np.zeros((944, 421, 3), dtype=np.uint8)
        template = bot.tpl("tutorial_hand_housing.png")
        height, width = template.shape[:2]
        altered = template.copy()
        cv2.rectangle(altered, (0, int(height * 0.62)), (width, height), (80, 80, 80), -1)
        phone[700:700 + height, 20:20 + width] = altered
        hit = {"loc": (20, 700), "w": width, "h": height}
        self.assertFalse(bot.tutorial_target_is_lit(phone, hit, (0.57, 0.74)))

    def test_hand_like_top_hud_match_is_rejected(self):
        phone = np.zeros((944, 421, 3), dtype=np.uint8)
        template = bot.tpl("tutorial_hand_housing.png")
        height, width = template.shape[:2]
        phone[40:40 + height, 250:250 + width] = template

        self.assertIsNone(bot.match_tutorial_hand(phone))

    def test_bell_hand_variant_is_found(self):
        phone = np.zeros((944, 421, 3), dtype=np.uint8)
        template = bot.tpl("tutorial_hand_bell.png")
        height, width = template.shape[:2]
        phone[340:340 + height, 225:225 + width] = template

        hit = bot.match_tutorial_hand(phone)

        self.assertIsNotNone(hit)
        self.assertEqual(hit["variant"], "tutorial_hand_bell.png")
        self.assertEqual(hit["loc"], (225, 340))

    def test_recommendation_hand_variant_is_found(self):
        phone = np.zeros((944, 421, 3), dtype=np.uint8)
        template = bot.tpl("tutorial_hand_recommend.png")
        height, width = template.shape[:2]
        phone[450:450 + height, 85:85 + width] = template

        hit = bot.match_tutorial_hand(phone)

        self.assertIsNotNone(hit)
        self.assertEqual(hit["variant"], "tutorial_hand_recommend.png")
        self.assertEqual(hit["loc"], (85, 450))

    def test_save_residents_hand_variant_is_found(self):
        phone = np.zeros((944, 421, 3), dtype=np.uint8)
        template = bot.tpl("tutorial_hand_save_residents.png")
        height, width = template.shape[:2]
        phone[475:475 + height, 225:225 + width] = template

        hit = bot.match_tutorial_hand(phone)

        self.assertIsNotNone(hit)
        self.assertEqual(hit["variant"], "tutorial_hand_save_residents.png")
        self.assertEqual(hit["loc"], (225, 475))

    def test_chest_hand_variant_is_found(self):
        phone = np.zeros((944, 421, 3), dtype=np.uint8)
        template = bot.tpl("tutorial_hand_chest.png")
        height, width = template.shape[:2]
        phone[430:430 + height, 205:205 + width] = template

        hit = bot.match_tutorial_hand(phone)

        self.assertIsNotNone(hit)
        self.assertEqual(hit["variant"], "tutorial_hand_chest.png")

    def test_city_task_hand_variant_is_found(self):
        phone = np.zeros((944, 421, 3), dtype=np.uint8)
        template = bot.tpl("tutorial_hand_task_center.png")
        height, width = template.shape[:2]
        phone[650:650 + height, 0:width] = template

        hit = bot.match_tutorial_hand(phone)

        self.assertIsNotNone(hit)
        self.assertEqual(hit["variant"], "tutorial_hand_task_center.png")

    def test_assign_quarry_hand_variant_is_found(self):
        phone = np.zeros((944, 421, 3), dtype=np.uint8)
        template = bot.tpl("tutorial_hand_assign_quarry.png")
        height, width = template.shape[:2]
        phone[660:660 + height, 105:105 + width] = template

        hit = bot.match_tutorial_hand(phone)

        self.assertIsNotNone(hit)
        self.assertEqual(hit["variant"], "tutorial_hand_assign_quarry.png")

    def test_assign_quarry_day_hand_variant_is_found(self):
        phone = np.zeros((944, 421, 3), dtype=np.uint8)
        template = bot.tpl("tutorial_hand_assign_quarry_day.png")
        height, width = template.shape[:2]
        phone[630:630 + height, 100:100 + width] = template

        hit = bot.match_tutorial_hand(phone)

        self.assertIsNotNone(hit)
        self.assertEqual(hit["variant"], "tutorial_hand_assign_quarry_day.png")

    def test_assign_quarry_after_upgrade_hand_variant_is_found(self):
        phone = np.zeros((944, 421, 3), dtype=np.uint8)
        template = bot.tpl("tutorial_hand_assign_quarry_after_upgrade.png")
        height, width = template.shape[:2]
        phone[630:630 + height, 100:100 + width] = template

        hit = bot.match_tutorial_hand(phone)

        self.assertIsNotNone(hit)
        self.assertEqual(
            hit["variant"], "tutorial_hand_assign_quarry_after_upgrade.png"
        )

    def test_assign_resident_slot_hand_variant_is_found(self):
        phone = np.zeros((944, 421, 3), dtype=np.uint8)
        template = bot.tpl("tutorial_hand_assign_resident_slot.png")
        height, width = template.shape[:2]
        phone[640:640 + height, 220:220 + width] = template

        hit = bot.match_tutorial_hand(phone)

        self.assertIsNotNone(hit)
        self.assertEqual(hit["variant"], "tutorial_hand_assign_resident_slot.png")

    def test_quarry_hand_core_survives_changed_scene_background(self):
        phone = np.zeros((944, 421, 3), dtype=np.uint8)
        template = bot.tpl("tutorial_hand_quarry_core.png")
        height, width = template.shape[:2]
        phone[640:640 + height, 140:140 + width] = template

        hit = bot.match_tutorial_hand(phone)

        self.assertIsNotNone(hit)
        self.assertEqual(hit["variant"], "tutorial_hand_quarry_core.png")

    def test_cook_button_is_found(self):
        phone = np.zeros((944, 421, 3), dtype=np.uint8)
        template = bot.tpl("tutorial_cook_button.png")
        height, width = template.shape[:2]
        phone[577:577 + height, 110:110 + width] = template

        hit = bot.match(phone, template, 0.94)

        self.assertIsNotNone(hit)
        self.assertEqual(hit["loc"], (110, 577))

    def test_primary_button_detector_finds_lower_tutorial_button(self):
        phone = np.zeros((944, 421, 3), dtype=np.uint8)
        cv2.rectangle(phone, (117, 878), (304, 926), (220, 210, 20), -1)

        hit = bot.find_tutorial_primary_button(phone)

        self.assertIsNotNone(hit)
        self.assertEqual(hit["loc"], (117, 878))
        self.assertGreaterEqual(hit["w"], 180)

    def test_upgrade_panel_mid_screen_button_and_parchment_are_detected(self):
        phone = np.zeros((944, 421, 3), dtype=np.uint8)
        phone[360:] = (160, 190, 220)
        cv2.rectangle(phone, (294, 457), (375, 507), (220, 210, 20), -1)

        hit = bot.find_tutorial_primary_button(phone)

        self.assertIsNotNone(hit)
        self.assertTrue(bot.is_construction_panel(phone))
        self.assertEqual(hit["loc"], (294, 457))

    def test_ocr_action_is_locked_until_its_text_disappears(self):
        phone = np.zeros((944, 421, 3), dtype=np.uint8)
        state = dict(bot.DEFAULT_STATE)
        target = {
            "action": "upgrade", "name": "tutorial_upgrade", "text": "Улучшить",
            "score": 99.0, "loc": (100, 700), "w": 150, "h": 50,
        }
        bot.LAST_OCR_AT = 0.0
        with patch("bot.ocr_action", return_value=target), patch("bot.ocr_action_is_safe", return_value=True), patch("bot.debug"), patch("bot.tap_match"), patch("bot.hold_match"), \
                patch("bot.set_step", side_effect=lambda s, step: s.update(step=step)), patch("bot.save_state"):
            first = bot.handle_tutorial_ocr(phone, state)
            bot.LAST_OCR_AT = 0.0
            second = bot.handle_tutorial_ocr(phone, state)

        self.assertEqual(first, "held")
        self.assertEqual(second, "wait")
        self.assertEqual(state["ocr_locked_action"], "upgrade")
        self.assertEqual(state["ocr_upgrade_hold_ms"], bot.UPGRADE_HOLD_MS)

    def test_upgrade_uses_a_long_hold_not_a_tap(self):
        phone = np.zeros((944, 421, 3), dtype=np.uint8)
        state = dict(bot.DEFAULT_STATE)
        target = {
            "action": "upgrade", "name": "tutorial_upgrade", "text": "Улучшить",
            "score": 99.0, "loc": (100, 700), "w": 150, "h": 50,
        }
        bot.LAST_OCR_AT = 0.0
        with patch("bot.ocr_action", return_value=target), patch("bot.ocr_action_is_safe", return_value=True), patch("bot.debug"), \
                patch("bot.tap_match") as tap_match, patch("bot.hold_match") as hold_match, \
                patch("bot.set_step", side_effect=lambda s, step: s.update(step=step)), patch("bot.save_state"):
            result = bot.handle_tutorial_ocr(phone, state)

        self.assertEqual(result, "held")
        tap_match.assert_not_called()
        hold_match.assert_called_once_with(phone, target, bot.UPGRADE_HOLD_MS)


if __name__ == "__main__":
    unittest.main()
