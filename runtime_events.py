"""Structured local event log for TUGARIN BOTS.

The human-readable bot.log remains useful for operators.  This JSONL stream is
stable enough for the GUI, diagnostics and long-run analysis to consume without
parsing Russian free-form text.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from typing import Any


ROOT = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(ROOT, "logs")
EVENT_FILE = os.path.join(LOG_DIR, "events.jsonl")
_LOCK = threading.Lock()


def emit_event(event: str, **payload: Any) -> dict[str, Any]:
    acceptance_run_id = os.environ.get("TUGARIN_ACCEPTANCE_RUN_ID", "").strip()
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": str(event),
        **({"run_id": acceptance_run_id} if acceptance_run_id else {}),
        **payload,
    }
    os.makedirs(LOG_DIR, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    with _LOCK:
        with open(EVENT_FILE, "a", encoding="utf-8") as stream:
            stream.write(line + "\n")
    return record


def read_recent_events(limit: int = 50) -> list[dict[str, Any]]:
    limit = max(1, int(limit))
    try:
        with open(EVENT_FILE, "r", encoding="utf-8") as stream:
            lines = stream.readlines()[-limit:]
    except OSError:
        return []

    result: list[dict[str, Any]] = []
    for line in lines:
        try:
            item = json.loads(line)
        except (TypeError, ValueError):
            continue
        if isinstance(item, dict):
            result.append(item)
    return result
