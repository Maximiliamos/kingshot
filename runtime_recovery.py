"""Bounded runtime recovery policy.

Recovery is deliberately conservative: transport failures may be retried, a
dead game may be relaunched a small number of times, but unknown game screens
remain owned by the visual fail-closed state machine.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RecoveryDecision:
    action: str
    detail: str = ""
    terminal: bool = False


class RecoveryController:
    def __init__(
        self,
        *,
        capture_threshold: int = 3,
        max_runtime_failures: int = 6,
        max_game_restarts: int = 2,
    ):
        self.capture_threshold = max(1, int(capture_threshold))
        self.max_runtime_failures = max(1, int(max_runtime_failures))
        self.max_game_restarts = max(0, int(max_game_restarts))
        self.capture_failures = 0
        self.runtime_failures = 0
        self.game_restarts = 0

    def capture_succeeded(self) -> None:
        self.capture_failures = 0
        self.runtime_failures = 0

    def capture_failed(self, backend: Any, error: str = "") -> RecoveryDecision:
        self.capture_failures += 1
        if self.capture_failures < self.capture_threshold:
            return RecoveryDecision(
                "retry_capture",
                f"capture failure {self.capture_failures}/{self.capture_threshold}: {error}",
            )

        self.capture_failures = 0
        health = backend.health()
        if not health.ready:
            self.runtime_failures += 1
            if self.runtime_failures >= self.max_runtime_failures:
                return RecoveryDecision(
                    "stop",
                    "Android runtime remained unavailable after bounded recovery attempts",
                    terminal=True,
                )
            return RecoveryDecision(
                "wait_runtime",
                f"Android not ready ({self.runtime_failures}/{self.max_runtime_failures})",
            )

        self.runtime_failures = 0
        if not health.package_running:
            if self.game_restarts >= self.max_game_restarts:
                return RecoveryDecision(
                    "stop",
                    "Kingshot is not running and automatic restart budget is exhausted",
                    terminal=True,
                )
            backend.launch_app()
            backend.wait_package_running(timeout=45)
            self.game_restarts += 1
            return RecoveryDecision(
                "game_restarted",
                f"Kingshot relaunched ({self.game_restarts}/{self.max_game_restarts})",
            )

        return RecoveryDecision(
            "reconnect_capture",
            "Android and Kingshot are healthy; recreate capture transport",
        )
