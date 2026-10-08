"""Fail-closed, non-destructive Kingshot resource-readiness gate.

A positive result means two separate observed frames contain corroborated,
recognizable *game* controls without an observed resource-error dialog. It
does not establish CDN reachability and does not guarantee that a subsequent
app-data clear will retain resources. Never click or reset during this probe.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

import numpy as np

from resource_diagnostics import collect_resource_network_diagnostics

CONFIRMED_ROLES = frozenset({
    "battle_reward_claim", "battle_conquer", "source_upgrade",
    "construction_primary", "construction_upgrade", "resident_add",
})
CONFIRMED_PANELS = frozenset({
    "battle_reward", "battle_conquest", "source_modal", "construction",
    "resident_assignment",
})


def evaluate_resource_samples(samples: list[dict[str, Any]], *, game_process_present: bool) -> dict[str, Any]:
    """Pure policy: unknown, black, or error screens may never authorize clear."""
    confirmed = []
    for sample in samples:
        confirmed.append(bool(
            sample.get("frame_valid")
            and not sample.get("resource_error")
            and not sample.get("account_restriction")
            and sample.get("known_game_ui")
        ))
    ready = bool(game_process_present and len(confirmed) >= 2 and all(confirmed))
    return {
        "pass": ready,
        "status": "observed_game_ui_ready" if ready else "resource_readiness_unconfirmed",
        "samples_confirmed": sum(confirmed),
        "samples_required": 2,
    }


def _observe(backend: Any) -> dict[str, Any]:
    import bot

    frame = backend.frame()
    if frame is None or not isinstance(frame, np.ndarray) or not frame.size:
        return {"frame_valid": False, "known_game_ui": False, "resource_error": False}
    phone, _, _ = bot.crop_phone(frame)
    frame_valid = bool(phone.size and float(np.std(phone)) > 5.0)
    if not frame_valid:
        return {"frame_valid": False, "known_game_ui": False, "resource_error": False}
    screen = bot.perceive_tutorial_screen(phone, include_ocr=True)
    normalized_ocr = " ".join(str(line.get("normalized", "")) for line in screen.ocr_lines)
    resource_error = bool(
        screen.panel.kind == "resource_error"
        or "неудалосьзагрузитьресурс" in normalized_ocr
    )
    roles = [button.role for button in screen.buttons if button.enabled and button.confidence >= 0.7]
    known_game_ui = bool(
        any(role in CONFIRMED_ROLES for role in roles)
        or (screen.panel.kind in CONFIRMED_PANELS and screen.panel.confidence >= 0.8)
        or (screen.tutorial_target is not None and screen.tutorial_target.confidence >= 0.85)
    )
    return {
        "frame_valid": frame_valid,
        "known_game_ui": known_game_ui,
        "resource_error": resource_error,
        "account_restriction": bool(bot.detect_stop_reason(phone)),
        "panel_kind": screen.panel.kind,
        "role_count": len([role for role in roles if role in CONFIRMED_ROLES]),
        "tutorial_target_confirmed": bool(
            screen.tutorial_target is not None and screen.tutorial_target.confidence >= 0.85
        ),
    }


def probe_resource_readiness(
    backend: Any, *, run_id: str = "", head: str = "", interval_seconds: float = 2.0
) -> dict[str, Any]:
    """Read-only: inspect device state and two separated production frames."""
    backend.require_ready()
    diag = collect_resource_network_diagnostics(backend, run_id=run_id, head=head)
    samples: list[dict[str, Any]] = []
    for index in range(2):
        if index:
            time.sleep(max(0.0, interval_seconds))
        try:
            samples.append(_observe(backend))
        except Exception as exc:
            # Never leak OCR, command output, URLs or account information.
            samples.append({"frame_valid": False, "known_game_ui": False,
                            "resource_error": False, "probe_error": type(exc).__name__})
    verdict = evaluate_resource_samples(
        samples,
        game_process_present=bool(diag["signals"].get("game_process_present")),
    )
    return {
        "schema": 1,
        "kind": "kingshot-resource-readiness",
        "head": head,
        "run_id": run_id,
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "non_destructive": True,
        "game_cdn_reachable": None,
        "diagnostic_status": diag["status"],
        "signals": diag["signals"],
        "probe_errors": diag["probe_errors"],
        "samples": samples,
        **verdict,
        "note": (
            "This proves only currently visible game UI. It does not prove "
            "resource CDN access after clearing app data. On failure, retain "
            "app data and collect operator evidence; never increase retry budget."
        ),
    }
