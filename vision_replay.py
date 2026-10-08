"""Replay marked-up *real* Kingshot frames through production perception.

Fixtures are independently reviewed: panel, safe/unsafe semantic roles and
bounding boxes. Missing files are errors, never skips. The empty bootstrap
manifest is explicitly NOT release-ready; no synthetic frame is counted as
real-host acceptance evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import cv2

from tutorial_vision import TutorialPerception
from task_engine import TASK_RULES


class ReplayError(ValueError):
    pass


def box_iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    intersection = max(0, min(ax + aw, bx + bw) - max(ax, bx)) * max(
        0, min(ay + ah, by + bh) - max(ay, by)
    )
    union = aw * ah + bw * bh - intersection
    return intersection / union if union > 0 else 0.0


def replay_manifest(manifest_path: str | Path, *, require_real_coverage: bool = False) -> dict[str, Any]:
    file = Path(manifest_path)
    manifest = json.loads(file.read_text(encoding="utf-8"))
    if manifest.get("schema") != 1 or not isinstance(manifest.get("samples"), list):
        raise ReplayError("Replay manifest schema=1 and samples[] required")
    positives = negatives = false_positives = 0
    failures = []
    cases = set()
    for case in manifest["samples"]:
        name = str(case.get("id", ""))
        if not name or name in cases:
            raise ReplayError("Replay samples require unique non-empty IDs")
        cases.add(name)
        kind = case.get("kind")
        if kind not in ("positive", "negative") or case.get("origin") != "real-host":
            raise ReplayError(f"{name}: only labeled real-host positive/negative cases accepted")
        source = str(case.get("image", ""))
        target = (file.parent / source).resolve()
        if not source or not target.is_relative_to(file.parent.resolve()) or not target.is_file():
            raise ReplayError(f"{name}: missing or out-of-root fixture")
        sha = hashlib.sha256(target.read_bytes()).hexdigest()
        if sha != case.get("sha256"):
            raise ReplayError(f"{name}: screenshot hash mismatch")
        frame = cv2.imread(str(target), cv2.IMREAD_COLOR)
        if frame is None or not frame.size:
            raise ReplayError(f"{name}: image is unreadable")
        model = TutorialPerception().perceive(frame, ocr_lines=case.get("ocr", []))
        observed = {
            item.role: item for item in model.buttons
            if item.enabled and item.role in TASK_RULES
        }
        if kind == "positive":
            positives += 1
            if model.panel.kind != case.get("panel"):
                failures.append(f"{name}: expected panel {case.get('panel')}, got {model.panel.kind}")
            for role, box in case.get("expected_roles", {}).items():
                item = observed.get(role)
                if item is None or box_iou(
                    (item.bbox.x, item.bbox.y, item.bbox.width, item.bbox.height),
                    tuple(box),
                ) < 0.4:
                    failures.append(f"{name}: missing/misplaced role {role}")
        else:
            negatives += 1
            forbidden = set(case.get("forbidden_roles", TASK_RULES))
            triggered = set(observed) & forbidden
            if triggered:
                false_positives += len(triggered)
                failures.append(f"{name}: unsafe false-positive roles {sorted(triggered)}")
    min_pos = int(manifest.get("min_positive_cases", 1))
    min_neg = int(manifest.get("min_negative_cases", 1))
    coverage_ready = positives >= min_pos and negatives >= min_neg
    if require_real_coverage and not coverage_ready:
        failures.append(f"real-frame coverage incomplete: positive={positives}, negative={negatives}")
    return {
        "schema": 1, "cases": len(cases), "positives": positives, "negatives": negatives,
        "false_positives": false_positives, "coverage_ready": coverage_ready,
        "pass": not failures, "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Kingshot real-frame replay")
    parser.add_argument("--manifest", default="tests/fixtures/tutorial_replay/manifest.json")
    parser.add_argument("--require-real-coverage", action="store_true")
    args = parser.parse_args()
    try:
        outcome = replay_manifest(args.manifest, require_real_coverage=args.require_real_coverage)
    except (ReplayError, OSError, ValueError, TypeError) as exc:
        print(json.dumps({"pass": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    return 0 if outcome["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
