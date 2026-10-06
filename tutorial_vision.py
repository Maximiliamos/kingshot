"""Reusable, fail-closed perception primitives for the Kingshot tutorial.

This module intentionally knows nothing about the bot phase/state machine.  It
turns pixels into a small screen model; policy code decides whether an action
is allowed.  Exact high-risk gates (state selection, rename, account dialogs)
remain outside this module.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Optional

import cv2
import numpy as np


@dataclass(frozen=True)
class Box:
    x: int
    y: int
    width: int
    height: int

    @property
    def center(self) -> tuple[int, int]:
        return self.x + self.width // 2, self.y + self.height // 2

    def as_hit(self) -> dict[str, Any]:
        return {"loc": (self.x, self.y), "w": self.width, "h": self.height}


@dataclass(frozen=True)
class Button:
    bbox: Box
    style: str
    enabled: bool
    confidence: float
    text: str = ""
    role: str = "generic"


@dataclass(frozen=True)
class Panel:
    kind: str
    confidence: float
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class TutorialTarget:
    bbox: Box
    confidence: float
    evidence: tuple[str, ...]
    source: str


@dataclass
class ScreenModel:
    tutorial_target: Optional[TutorialTarget] = None
    buttons: list[Button] = field(default_factory=list)
    panel: Panel = field(default_factory=lambda: Panel("city_home", 0.5, ()))
    ocr_lines: list[dict[str, Any]] = field(default_factory=list)
    rejected_targets: list[dict[str, Any]] = field(default_factory=list)

    def button(self, role: str) -> Optional[Button]:
        candidates = [item for item in self.buttons if item.role == role]
        return max(candidates, key=lambda item: item.confidence, default=None)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _coverage(mask: np.ndarray) -> float:
    return float(np.count_nonzero(mask)) / max(1.0, float(mask.size))


class UniversalButtonDetector:
    """Detect button-shaped controls; semantics come from panel + OCR context."""

    STYLES = {
        "cyan": ((75, 80, 80), (105, 255, 255)),
        "green": ((35, 90, 70), (95, 255, 255)),
        "grey": ((0, 0, 55), (179, 85, 190)),
    }

    def detect(self, frame: np.ndarray) -> list[Button]:
        height, width = frame.shape[:2]
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        buttons: list[Button] = []
        for style, (lower, upper) in self.STYLES.items():
            mask = cv2.inRange(hsv, lower, upper)
            mask = cv2.morphologyEx(
                mask,
                cv2.MORPH_CLOSE,
                cv2.getStructuringElement(cv2.MORPH_RECT, (7, 5)),
            )
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for contour in contours:
                x, y, w, h = cv2.boundingRect(contour)
                aspect = w / max(1.0, float(h))
                if w < width * 0.055 or h < height * 0.025 or not 0.45 <= aspect <= 8.0:
                    continue
                fill = cv2.contourArea(contour) / max(1.0, float(w * h))
                if fill < 0.30:
                    continue
                buttons.append(
                    Button(
                        bbox=Box(x, y, w, h),
                        style=style,
                        # Colour alone does not define enabled state: Kingshot uses
                        # active grey hold-buttons in construction panels. Semantic
                        # classification below marks truly disabled controls.
                        enabled=True,
                        confidence=min(1.0, 0.45 + fill * 0.55),
                    )
                )
        return buttons


class PanelDetector:
    def detect(self, frame: np.ndarray, buttons: list[Button], ocr: Iterable[dict]) -> Panel:
        height, width = frame.shape[:2]
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        normalized = " ".join(str(line.get("normalized", "")) for line in ocr)

        modal = hsv[round(height * 0.31):round(height * 0.64), round(width * 0.08):round(width * 0.92)]
        modal_beige = _coverage(cv2.inRange(modal, (8, 8, 90), (35, 150, 255)))
        modal_cyan = [
            item for item in buttons
            if item.style == "cyan"
            and width * 0.62 <= item.bbox.center[0] <= width * 0.90
            and height * 0.38 <= item.bbox.center[1] <= height * 0.62
        ]
        if modal_beige >= 0.42 and len(modal_cyan) == 2:
            return Panel("source_modal", min(1.0, 0.55 + modal_beige * 0.45), ("beige-modal", "two-actions"))

        lower = hsv[round(height * 0.38):round(height * 0.96)]
        lower_beige = _coverage(cv2.inRange(lower, (8, 8, 90), (35, 150, 255)))
        # A resident assignment panel always has its two-tab footer.  City
        # Center's green benefit icons occupy the same relative position as
        # the add-resident control, so colour/geometry alone is unsafe.
        footer = hsv[round(height * 0.90):round(height * 0.98), round(width * 0.05):round(width * 0.95)]
        resident_footer = _coverage(cv2.inRange(footer, (5, 30, 20), (30, 255, 200)))
        resident_controls = [
            item for item in buttons
            if width * 0.54 <= item.bbox.center[0] <= width * 0.72
            and height * 0.72 <= item.bbox.center[1] <= height * 0.90
            and item.style in ("green", "grey")
            and 0.45 <= item.bbox.width / max(1.0, float(item.bbox.height)) <= 1.25
        ]
        resident_green = any(item.style == "green" for item in resident_controls)
        resident_disabled_cell = any(
            item.style == "grey"
            and width * 0.58 <= item.bbox.center[0] <= width * 0.68
            and height * 0.80 <= item.bbox.center[1] <= height * 0.87
            for item in resident_controls
        )
        if lower_beige >= 0.48 and resident_footer >= 0.20 and (resident_green or resident_disabled_cell):
            return Panel("resident_assignment", min(1.0, 0.50 + lower_beige * 0.50), ("lower-beige", "resident-footer", "resident-control"))

        construction_words = ("кухня", "требуется", "улучшить", "барак")
        primary = [
            item for item in buttons
            if item.style in ("cyan", "grey")
            and item.bbox.width >= width * 0.18
            and item.bbox.center[1] >= height * 0.40
        ]
        if any(word in normalized for word in construction_words) and primary:
            return Panel("construction", 0.96, ("ocr", "button-geometry"))
        if lower_beige >= 0.45 and primary:
            return Panel("construction", min(0.91, 0.55 + lower_beige * 0.50), ("parchment", "button-geometry"))
        if modal_beige >= 0.30:
            return Panel("tutorial_dialog", min(0.80, 0.45 + modal_beige), ("central-beige",))
        return Panel("city_home", 0.55, ("no-modal-panel",))


class TutorialGuidanceDetector:
    """Find the illuminated tutorial target, using animation as confirmation.

    The detector is background-independent.  A legacy pointer match may be
    supplied as corroborating evidence, but the returned click is a target
    coordinate and no new background template is required.
    """

    def _circle_candidates(self, frame: np.ndarray, previous: Optional[np.ndarray]) -> list[dict[str, Any]]:
        height, width = frame.shape[:2]
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        # Hough is the most expensive primitive.  Run it on a bounded-width
        # image, then score candidates against the original pixels.
        hough_scale = min(1.0, 280.0 / max(1.0, float(width)))
        hough_frame = (
            cv2.resize(frame, None, fx=hough_scale, fy=hough_scale, interpolation=cv2.INTER_AREA)
            if hough_scale < 1.0 else frame
        )
        gray = cv2.GaussianBlur(cv2.cvtColor(hough_frame, cv2.COLOR_BGR2GRAY), (7, 7), 1.5)
        circles = cv2.HoughCircles(
            gray, cv2.HOUGH_GRADIENT, 1.2, 24,
            param1=100, param2=28,
            minRadius=max(7, round(width * 0.028 * hough_scale)),
            maxRadius=max(16, round(width * 0.16 * hough_scale)),
        )
        if circles is None:
            return []
        yy, xx = np.ogrid[:height, :width]
        lit_pixels = (
            (hsv[:, :, 0] >= 15) & (hsv[:, :, 0] <= 45)
            & (hsv[:, :, 1] >= 30) & (hsv[:, :, 2] >= 130)
        )
        delta = None
        global_motion = 0.0
        if previous is not None and previous.shape == frame.shape:
            delta = cv2.cvtColor(cv2.absdiff(frame, previous), cv2.COLOR_BGR2GRAY)
            global_motion = _coverage(cv2.inRange(delta, 12, 255))
        accepted: list[dict[str, Any]] = []
        for raw_x, raw_y, raw_radius in circles[0]:
            x = round(float(raw_x) / hough_scale)
            y = round(float(raw_y) / hough_scale)
            radius = round(float(raw_radius) / hough_scale)
            if y < height * 0.22 or y > height * 0.94:
                continue
            distance = np.sqrt((xx - x) ** 2 + (yy - y) ** 2)
            ring = (distance >= radius * 0.58) & (distance <= radius * 1.20)
            glow = float(np.count_nonzero(lit_pixels & ring)) / max(1.0, float(np.count_nonzero(ring)))
            x0, x1 = max(0, x - radius), min(width, x + round(radius * 2.3))
            y0, y1 = max(0, y - radius * 3), min(height, y + round(radius * 0.35))
            pointer_roi = hsv[y0:y1, x0:x1]
            if pointer_roi.size == 0:
                continue
            white = _coverage(cv2.inRange(pointer_roi, (0, 0, 155), (179, 95, 255)))
            cuff = _coverage(cv2.inRange(pointer_roi, (4, 90, 100), (35, 255, 255)))
            motion = 0.0
            if delta is not None:
                motion = _coverage(cv2.inRange(delta[y0:y1, x0:x1], 12, 255))
            confidence = min(1.0, glow * 1.55 + white * 0.55 + cuff * 0.50 + min(motion, 0.25))
            evidence = []
            if glow >= 0.20:
                evidence.append("target-glow")
            if white >= 0.12 and cuff >= 0.035:
                evidence.append("pointer-colors")
            temporal_motion = motion >= 0.015 and global_motion <= 0.08
            if temporal_motion:
                evidence.append("temporal-motion")
            candidate = {
                "center": (x, y), "radius": radius, "confidence": confidence,
                "glow": glow, "white": white, "cuff": cuff, "motion": motion,
                "global_motion": global_motion, "temporal_motion": temporal_motion,
                "evidence": evidence,
            }
            accepted.append(candidate)
        return accepted

    def detect(
        self,
        frame: np.ndarray,
        previous: Optional[np.ndarray] = None,
        legacy_hint: Optional[dict[str, Any]] = None,
    ) -> tuple[Optional[TutorialTarget], list[dict[str, Any]]]:
        candidates = self._circle_candidates(frame, previous)
        rejected: list[dict[str, Any]] = []
        confirmed = [
            item for item in candidates
            if item["glow"] >= 0.20
            and item["white"] >= 0.12
            and item["cuff"] >= 0.035
            and (item["motion"] >= 0.015 or item["confidence"] >= 0.78)
        ]
        if legacy_hint:
            hx, hy = legacy_hint["loc"]
            tx, ty = legacy_hint.get("target", (0.5, 0.5))
            expected_x = round(hx + legacy_hint["w"] * tx)
            expected_y = round(hy + legacy_hint["h"] * ty)
            nearby = [
                item for item in confirmed
                if np.hypot(item["center"][0] - expected_x, item["center"][1] - expected_y)
                <= max(45.0, frame.shape[1] * 0.16)
            ]
            if nearby:
                best = min(
                    nearby,
                    key=lambda item: np.hypot(
                        item["center"][0] - expected_x,
                        item["center"][1] - expected_y,
                    ),
                )
                radius = max(10, round(best["radius"] * 0.55))
                x, y = best["center"]
                return TutorialTarget(
                    bbox=Box(x - radius, y - radius, radius * 2, radius * 2),
                    confidence=float(best["confidence"]),
                    evidence=tuple(best["evidence"] + ["legacy-pointer-confirmation"]),
                    source="universal-glow-pointer",
                ), rejected

        # A novel static frame is intentionally insufficient: city artwork
        # contains many circular yellow/white objects.  New targets require
        # animation evidence, while known scenes can use the migration hint.
        novel = [item for item in confirmed if item["temporal_motion"]]
        if novel:
            best = max(novel, key=lambda item: item["confidence"])
            radius = max(10, round(best["radius"] * 0.55))
            x, y = best["center"]
            return TutorialTarget(
                bbox=Box(x - radius, y - radius, radius * 2, radius * 2),
                confidence=float(best["confidence"]),
                evidence=tuple(best["evidence"]),
                source="universal-glow-pointer",
            ), rejected

        rejected.extend(candidates)
        if legacy_hint:
            # Migration fallback: existing screenshots remain covered while
            # state-machine code no longer depends on the template identity.
            hx, hy = legacy_hint["loc"]
            tx, ty = legacy_hint.get("target", (0.5, 0.5))
            cx = round(hx + legacy_hint["w"] * tx)
            cy = round(hy + legacy_hint["h"] * ty)
            radius = max(10, round(min(legacy_hint["w"], legacy_hint["h"]) * 0.08))
            return TutorialTarget(
                bbox=Box(cx - radius, cy - radius, radius * 2, radius * 2),
                confidence=min(0.89, max(0.70, float(legacy_hint.get("score", 0.0)))),
                evidence=("target-glow", "legacy-pointer-fallback"),
                source="legacy-fallback",
            ), rejected
        return None, rejected


class TutorialPerception:
    def __init__(self):
        self.buttons = UniversalButtonDetector()
        self.panels = PanelDetector()
        self.guidance = TutorialGuidanceDetector()
        self.previous: Optional[np.ndarray] = None

    @staticmethod
    def _bind_ocr(buttons: list[Button], ocr: list[dict[str, Any]]) -> list[Button]:
        """Attach OCR text to the nearest containing button without creating actions.

        OCR remains evidence only. Roles are still assigned from panel context and
        geometry, so arbitrary text can never turn into a blind click target.
        """
        bound: list[Button] = []
        for button in buttons:
            bx0, by0 = button.bbox.x, button.bbox.y
            bx1 = bx0 + button.bbox.width
            by1 = by0 + button.bbox.height
            pad_x = max(4, round(button.bbox.width * 0.18))
            pad_y = max(3, round(button.bbox.height * 0.35))
            matches: list[tuple[float, dict[str, Any]]] = []
            for line in ocr:
                loc = line.get("loc") or (0, 0)
                lw, lh = int(line.get("w", 0) or 0), int(line.get("h", 0) or 0)
                cx = float(loc[0]) + lw / 2.0
                cy = float(loc[1]) + lh / 2.0
                if not (bx0 - pad_x <= cx <= bx1 + pad_x and by0 - pad_y <= cy <= by1 + pad_y):
                    continue
                distance = np.hypot(cx - button.bbox.center[0], cy - button.bbox.center[1])
                matches.append((float(distance), line))
            text = button.text
            if matches:
                _, best = min(matches, key=lambda item: item[0])
                text = str(best.get("text", "") or "").strip()
            bound.append(Button(button.bbox, button.style, button.enabled, button.confidence, text, button.role))
        return bound

    def perceive(
        self,
        frame: np.ndarray,
        *,
        ocr_lines: Optional[list[dict[str, Any]]] = None,
        legacy_hint: Optional[dict[str, Any]] = None,
    ) -> ScreenModel:
        ocr = list(ocr_lines or [])
        buttons = self._bind_ocr(self.buttons.detect(frame), ocr)
        panel = self.panels.detect(frame, buttons, ocr)
        target, rejected = self.guidance.detect(frame, self.previous, legacy_hint)
        self.previous = frame.copy()

        # Assign semantic roles only when independent context agrees.
        classified: list[Button] = []
        source_buttons = sorted(
            [
                item for item in buttons
                if item.style == "cyan"
                and panel.kind == "source_modal"
                and frame.shape[1] * 0.62 <= item.bbox.center[0] <= frame.shape[1] * 0.90
                and frame.shape[0] * 0.38 <= item.bbox.center[1] <= frame.shape[0] * 0.62
            ],
            key=lambda item: item.bbox.y,
        )
        for item in buttons:
            role = item.role
            if panel.kind == "source_modal" and item in source_buttons[:1]:
                role = "source_upgrade"
            elif (
                panel.kind == "construction"
                and item.style in ("cyan", "grey")
                and item.bbox.width >= frame.shape[1] * 0.18
                and item.bbox.center[1] >= frame.shape[0] * 0.40
            ):
                normalized_text = "".join(ch for ch in item.text.lower() if ch.isalnum())
                role = (
                    "construction_upgrade"
                    if item.style == "grey" or "улучш" in normalized_text
                    else "construction_primary"
                )
            enabled = item.enabled
            if role in ("construction_primary", "construction_upgrade"):
                # Grey construction controls are active hold actions in Kingshot;
                # panel context, not colour, is the independent enabling evidence.
                enabled = True
            classified.append(
                Button(item.bbox, item.style, enabled, item.confidence, item.text, role)
            )
        if panel.kind == "resident_assignment":
            height, width = frame.shape[:2]
            x0, x1 = round(width * 0.58), round(width * 0.68)
            y0, y1 = round(height * 0.80), round(height * 0.87)
            cell = cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2HSV)
            green = _coverage(cv2.inRange(cell, (35, 90, 70), (95, 255, 255)))
            neutral = _coverage(cv2.inRange(cell, (0, 0, 55), (179, 85, 190)))
            if green >= 0.05:
                classified.append(Button(Box(x0, y0, x1 - x0, y1 - y0), "green", True, green, role="resident_add"))
            elif neutral >= 0.30:
                classified.append(Button(Box(x0, y0, x1 - x0, y1 - y0), "grey", False, neutral, role="resident_complete"))
        return ScreenModel(target, classified, panel, ocr, rejected)


class BoundedActionPolicy:
    """One persistence-friendly retry policy for repeated tutorial controls."""

    @staticmethod
    def signature(kind: str, bbox: Box, frame_shape: tuple[int, ...]) -> list[Any]:
        height, width = frame_shape[:2]
        def bucket(value, total):
            percent = value / max(1, total) * 100
            return round(percent / 3.0) * 3
        return [
            kind,
            bucket(bbox.center[0], width),
            bucket(bbox.center[1], height),
            bucket(bbox.width, width),
            bucket(bbox.height, height),
        ]

    def decide(
        self,
        state: dict[str, Any],
        kind: str,
        bbox: Box,
        frame_shape: tuple[int, ...],
        *,
        retry_after: float = 3.0,
        now: Optional[float] = None,
    ) -> str:
        import time

        timestamp = time.time() if now is None else float(now)
        signature = self.signature(kind, bbox, frame_shape)
        locked = state.get("tutorial_action_lock") or {}
        if locked.get("signature") != signature:
            state["tutorial_action_lock"] = {"signature": signature, "acted_at": timestamp, "attempts": 1}
            return "act"
        if timestamp - float(locked.get("acted_at", 0.0)) < retry_after:
            return "wait"
        attempts = int(locked.get("attempts", 1))
        if attempts >= 2:
            return "exhausted"
        locked.update(acted_at=timestamp, attempts=attempts + 1)
        state["tutorial_action_lock"] = locked
        return "retry"

    @staticmethod
    def clear_if_absent(state: dict[str, Any], visible_kind: Optional[str]) -> bool:
        locked = state.get("tutorial_action_lock") or {}
        signature = locked.get("signature") or []
        if signature and signature[0] != visible_kind:
            state.pop("tutorial_action_lock", None)
            return True
        return False

    @staticmethod
    def clear_kind(state: dict[str, Any], kind: str) -> bool:
        locked = state.get("tutorial_action_lock") or {}
        signature = locked.get("signature") or []
        if signature and signature[0] == kind:
            state.pop("tutorial_action_lock", None)
            return True
        return False
