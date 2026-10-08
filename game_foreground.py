"""Read-only foreground evidence for Kingshot on the selected Android device.

The WSA desktop capture can contain both game pixels and a Google Play window.
A running Kingshot PID alone therefore cannot authorize game UI readiness.
Read only Android focus/task dumps, and persist category labels *only*.
Never persist package names from arbitrary foreground apps or raw dumpsys data.
"""
from __future__ import annotations

import re
from typing import Any

# Package/activity syntax, not a URL or a Windows window-title guess.
_ACTIVITY_PACKAGE = re.compile(
    r"\b([A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_][A-Za-z_0-9]*)+)/"
)
_WINDOW_FOCUS = re.compile(r"(?m)^\s*mCurrentFocus\s*=\s*(.*?)\s*$")
_FOCUSED_APP = re.compile(r"(?m)^\s*mFocusedApp\s*=\s*(.*?)\s*$")
_TOP_RESUMED = re.compile(
    r"(?m)^\s*(?:topResumedActivity|mResumedActivity|ResumedActivity)\s*[:=]\s*(.*?)\s*$"
)


def _label(raw: str, package: str) -> str:
    """Return game/other/unknown, never an actual package name."""
    value = raw.strip()
    if not value or value.lower() in ("null", "none"):
        return "unknown"
    match = _ACTIVITY_PACKAGE.search(value)
    if match:
        return "game" if match.group(1) == package else "other"
    # A non-null focused *window* without an Android activity component, e.g.
    # StatusBar or an interstitial window, is not proof of Kingshot foreground.
    return "other"


def classify_foreground(window_dump: str, activity_dump: str, package: str) -> dict[str, Any]:
    """Classify read-only window and ActivityTaskManager focus without leaking names."""
    current = _WINDOW_FOCUS.findall(window_dump)
    focus_values = current if current else _FOCUSED_APP.findall(window_dump)
    resumed_values = _TOP_RESUMED.findall(activity_dump)

    window_label = _label(focus_values[0], package) if focus_values else "unknown"
    # topResumedActivity is authoritative when present; old Android versions
    # expose mResumedActivity or ResumedActivity instead.
    activity_label = _label(resumed_values[0], package) if resumed_values else "unknown"

    # Multiple contradictory snapshots in one dump must never authorize input.
    focus_conflict = len({_label(s, package) for s in focus_values if _label(s, package) != "unknown"}) > 1
    activity_conflict = len({_label(s, package) for s in resumed_values if _label(s, package) != "unknown"}) > 1
    known = {kind for kind in (window_label, activity_label) if kind != "unknown"}
    if focus_conflict or activity_conflict or len(known) > 1:
        status = "conflict"
    elif known == {"game"}:
        status = "game"
    elif known == {"other"}:
        status = "other"
    else:
        status = "unknown"

    return {
        "foreground_state": status,
        "foreground_confirmed": status == "game",
        "window_focus_kind": window_label,
        "resumed_activity_kind": activity_label,
    }


def collect_foreground_evidence(backend: Any) -> dict[str, Any]:
    """Read two bounded Android state dumps; raw output and stderr stay in memory."""
    errors: dict[str, str] = {}

    def query(name: str, command: list[str]) -> str:
        try:
            return str(backend.shell(command, timeout=8) or "")
        except Exception as exc:
            # Only the exception *type*: stdout/stderr may contain device IDs.
            errors[name] = type(exc).__name__
            return ""

    window = query("window", ["dumpsys", "window", "windows"])
    activity = query("activity", ["dumpsys", "activity", "activities"])
    result = classify_foreground(
        window, activity, str(getattr(backend, "package", "com.got.globalru"))
    )
    result["foreground_probe_errors"] = errors
    return result
