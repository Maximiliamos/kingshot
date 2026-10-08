"""Vision replay harness regressions using synthetic unit frames only.

These unit tests do not constitute the required real-host image corpus.
"""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from tutorial_vision import Box, Button, Panel, ScreenModel
from vision_replay import ReplayError, box_iou, replay_manifest


class ReplayHarnessTests(unittest.TestCase):
    def test_box_iou(self):
        self.assertAlmostEqual(box_iou((0, 0, 10, 10), (0, 0, 10, 10)), 1.0)
        self.assertEqual(box_iou((0, 0, 10, 10), (20, 20, 10, 10)), 0.0)

    def create_case(self, root, *, kind, sha=None, role="battle_reward_claim"):
        image = root / "frame.png"
        assert cv2.imwrite(str(image), np.zeros((944, 421, 3), np.uint8))
        digest = hashlib.sha256(image.read_bytes()).hexdigest()
        case = {
            "id": "case-1", "kind": kind, "origin": "real-host",
            "image": "frame.png", "sha256": sha or digest,
            "ocr": [], "panel": "battle_reward",
            "expected_roles": {role: [100, 250, 100, 40]},
            "forbidden_roles": [role],
        }
        path = root / "manifest.json"
        path.write_text(json.dumps({
            "schema": 1, "samples": [case],
            "min_positive_cases": 1, "min_negative_cases": 1,
        }), encoding="utf-8")
        return path

    def positive_model(self):
        return ScreenModel(
            panel=Panel("battle_reward", 0.95),
            buttons=[Button(Box(100, 250, 100, 40), "green", True, 0.95, role="battle_reward_claim")],
        )

    def test_positive_bbox_and_panel(self):
        with tempfile.TemporaryDirectory() as temp:
            p = self.create_case(Path(temp), kind="positive")
            with patch("vision_replay.TutorialPerception") as perceive:
                perceive.return_value.perceive.return_value = self.positive_model()
                result = replay_manifest(p)
            self.assertTrue(result["pass"])
            self.assertEqual(result["positives"], 1)
            self.assertFalse(result["coverage_ready"])

    def test_unsafe_negative_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            p = self.create_case(Path(temp), kind="negative")
            with patch("vision_replay.TutorialPerception") as perceive:
                perceive.return_value.perceive.return_value = self.positive_model()
                result = replay_manifest(p)
            self.assertFalse(result["pass"])
            self.assertEqual(result["false_positives"], 1)

    def test_fixture_hash_mismatch_blocks(self):
        with tempfile.TemporaryDirectory() as temp:
            p = self.create_case(Path(temp), kind="negative", sha="0" * 64)
            with self.assertRaisesRegex(ReplayError, "hash mismatch"):
                replay_manifest(p)

    def test_empty_manifest_never_passes_release_coverage(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp) / "manifest.json"
            p.write_text(json.dumps({
                "schema": 1, "samples": [],
                "min_positive_cases": 4, "min_negative_cases": 4,
            }), encoding="utf-8")
            self.assertFalse(replay_manifest(p)["coverage_ready"])
            self.assertFalse(replay_manifest(p, require_real_coverage=True)["pass"])

    def test_path_traversal_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            p = self.create_case(Path(temp), kind="positive")
            data = json.loads(p.read_text(encoding="utf-8"))
            data["samples"][0]["image"] = "../../some-other-file.png"
            p.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(ReplayError, "missing or out-of-root"):
                replay_manifest(p)


if __name__ == "__main__":
    unittest.main()
