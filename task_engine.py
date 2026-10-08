"""Declarative, fail-closed tasks for already-recognized game UI controls.

Targets must originate from verified perception. High-risk kingdom selection,
character renaming and resource retries intentionally remain separate.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from tutorial_vision import BoundedActionPolicy, ScreenModel


@dataclass(frozen=True)
class TaskRule:
    role: str
    panels: frozenset[str]
    action: str
    next_step: str
    postcondition: str = "recognized_role_disappears"
    minimum_confidence: float = 0.7
    retry_after: float = 3.0
    requires_enabled: bool = True


@dataclass(frozen=True)
class TaskDecision:
    rule: TaskRule
    status: str
    hit: dict[str, Any]


TASK_RULES: dict[str, TaskRule] = {
    "battle_reward_claim": TaskRule(
        "battle_reward_claim", frozenset({"battle_reward"}),
        "tap", "tutorial_wait_hand_result",
    ),
    "battle_conquer": TaskRule(
        "battle_conquer", frozenset({"battle_reward", "battle_conquest"}),
        "tap", "tutorial_wait_hand_result",
    ),
    "construction_upgrade": TaskRule(
        "construction_upgrade", frozenset({"construction"}),
        "hold", "tutorial_wait_scroll",
    ),
    "construction_primary": TaskRule(
        "construction_primary", frozenset({"construction"}),
        "tap", "tutorial_wait_construction",
    ),
    "source_upgrade": TaskRule(
        "source_upgrade", frozenset({"source_modal"}),
        "tap", "tutorial_wait_hand_result",
    ),
    "resident_add": TaskRule(
        "resident_add", frozenset({"resident_assignment"}),
        "tap", "tutorial_wait_hand_result",
    ),
    "resident_complete": TaskRule(
        "resident_complete", frozenset({"resident_assignment"}),
        "key_back", "tutorial_wait_hand_result",
        postcondition="resident_assignment_panel_closes",
        requires_enabled=False,
    ),
}


class SemanticTaskEngine:
    def __init__(self, policy: BoundedActionPolicy | None = None):
        self.policy = policy or BoundedActionPolicy()

    def plan(
        self,
        state: dict[str, Any],
        screen: ScreenModel,
        role: str,
        frame_shape: tuple[int, ...],
        *,
        now: float | None = None,
    ) -> TaskDecision | None:
        rule = TASK_RULES.get(role)
        if rule is None or screen.panel.kind not in rule.panels:
            return None
        button = screen.button(role)
        if (button is None or (rule.requires_enabled and not button.enabled)
                or button.confidence < rule.minimum_confidence):
            return None
        box = button.bbox
        if (box.width <= 0 or box.height <= 0 or box.x < 0 or box.y < 0
                or box.x + box.width > frame_shape[1]
                or box.y + box.height > frame_shape[0]):
            return None
        status = self.policy.decide(
            state, role, box, frame_shape, retry_after=rule.retry_after, now=now,
        )
        return TaskDecision(rule, status, box.as_hit())
