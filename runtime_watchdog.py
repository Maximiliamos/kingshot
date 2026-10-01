"""Lightweight heartbeat for operator visibility and watchdog evidence."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from typing import Any


ROOT = os.path.dirname(os.path.abspath(__file__))
DEBUG_DIR = os.path.join(ROOT, "debug")
HEARTBEAT_FILE = os.path.join(DEBUG_DIR, "runtime-heartbeat.json")


class RuntimeHeartbeat:
    def __init__(self, *, path: str = HEARTBEAT_FILE, interval: float = 2.0):
        self.path = path
        self.interval = max(0.2, float(interval))
        self.last_write = 0.0
        self.last_frame_monotonic = 0.0
        self.last_action_monotonic = 0.0

    def mark_frame(self) -> None:
        self.last_frame_monotonic = time.monotonic()

    def mark_action(self) -> None:
        self.last_action_monotonic = time.monotonic()

    def write(
        self,
        *,
        state: dict[str, Any],
        backend: str,
        serial: str,
        force: bool = False,
        status: str = "running",
        detail: str = "",
    ) -> bool:
        now = time.monotonic()
        if not force and now - self.last_write < self.interval:
            return False

        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        payload = {
            "schema": 1,
            "written_at": datetime.now(timezone.utc).isoformat(),
            "status": status,
            "detail": detail,
            "pid": os.getpid(),
            "backend": backend,
            "serial": serial,
            "phase": state.get("phase", ""),
            "step": state.get("step", ""),
            "last_frame_age_seconds": (
                None
                if not self.last_frame_monotonic
                else round(max(0.0, now - self.last_frame_monotonic), 3)
            ),
            "last_action_age_seconds": (
                None
                if not self.last_action_monotonic
                else round(max(0.0, now - self.last_action_monotonic), 3)
            ),
        }
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)
        self.last_write = now
        return True
