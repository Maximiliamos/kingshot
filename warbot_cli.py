"""Agent-friendly CLI for the unified WAR BOT Android backend."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

import cv2
import numpy as np

from device_backend import BackendError, NativeArm64Backend, WsaBackend, create_backend
from frame_stream import create_preview_capture
from scrcpy_transport import ScrcpyServerCapture, default_server_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TUGARIN BOTS Android backend CLI")
    parser.add_argument(
        "action",
        choices=(
            "status", "screenshot", "bootstrap", "install-game", "launch-game", "stop-game",
            "restart-game", "preview-smoke", "preview-probe",
            "google-services-smoke",
            "restriction-check",
            "clear-game-data", "clean-start", "prepare-mvp-flow", "flow-evidence",
            "prepare-mvp-soak", "soak-evidence", "recovery-smoke",
            "operator-io-smoke", "resource-diagnostics", "resource-readiness", "tap", "swipe", "ui-dump",
            "start-runtime", "stop-runtime",
        ),
    )
    parser.add_argument("--backend", default="wsa")
    parser.add_argument("--serial", default=None)
    parser.add_argument("--adb", default=None)
    parser.add_argument("--output", default="warbot-frame.png")
    parser.add_argument("--report", default="")
    parser.add_argument("--apks-dir", default=None)
    parser.add_argument("--game-stability-seconds", type=int, default=45)
    parser.add_argument("--preview-seconds", type=float, default=10.0)
    parser.add_argument("--require-h264", action="store_true")
    parser.add_argument("--require-fast", action="store_true")
    parser.add_argument("--require-printwindow", action="store_true")
    parser.add_argument("--min-preview-fps", type=float, default=15.0)
    parser.add_argument("--with-adb-reconnect", action="store_true")
    parser.add_argument("--min-characters", type=int, default=2)
    parser.add_argument("--wipe", action="store_true")
    parser.add_argument(
        "--clean-game",
        action="store_true",
        help="Explicitly clear game app data after install/readiness gate.",
    )
    parser.add_argument("--window", action="store_true")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("coords", nargs="*", type=int)
    return parser


def reset_workflow_for_clean_game() -> dict:
    """Reset only Android-dependent workflow while preserving PC counters."""
    import bot
    old = bot.load_state()
    fresh = dict(bot.DEFAULT_STATE)
    for key in (
        "next_nickname", "characters_created", "current_cycle",
        "characters_per_cycle", "auto_reset_data", "repeat_cycles",
        "target_state",
    ):
        if key in old:
            fresh[key] = old[key]
    fresh["pending_nickname"] = int(fresh.get("next_nickname", 1))
    fresh["characters_created_cycle"] = 0
    fresh["last_stop_reason"] = ""
    fresh["tutorial_origin"] = "initial"
    bot.save_state(fresh)
    return fresh


def loading_logo_visible(frame) -> bool:
    """Return True when the verified Kingshot loading-logo template is visible.

    Keep the P0 runtime gate independent from bot.py: importing the full bot
    also loads desktop-capture/UI dependencies that are irrelevant to a pure
    ADB bootstrap and can fail before the 120-second game stability test.
    """
    template_path = Path(__file__).resolve().parent / "templates" / "loading_logo.png"
    if not template_path.is_file():
        raise BackendError(f"Loading-logo template is missing: {template_path}")

    template = cv2.imread(str(template_path), cv2.IMREAD_COLOR)
    if template is None or template.size == 0:
        raise BackendError(f"Loading-logo template is invalid: {template_path}")

    if frame is None or frame.size == 0:
        raise BackendError("Cannot evaluate loading screen from an empty framebuffer")

    height, width = frame.shape[:2]
    target_ratio = 1060 / 2376
    actual_ratio = width / max(1, height)
    if actual_ratio > target_ratio:
        content_width = max(1, round(height * target_ratio))
        left = max(0, (width - content_width) // 2)
        phone = frame[:, left:left + content_width]
    else:
        content_height = max(1, round(width / target_ratio))
        top = max(0, (height - content_height) // 2)
        phone = frame[top:top + content_height, :]

    phone = cv2.resize(phone, (421, 944), interpolation=cv2.INTER_AREA)
    best = -1.0
    for scale in (0.72, 0.80, 0.88, 0.94, 1.0, 1.06, 1.12, 1.18, 1.25, 1.32):
        candidate = template if scale == 1.0 else cv2.resize(
            template,
            None,
            fx=scale,
            fy=scale,
            interpolation=cv2.INTER_AREA if scale < 1.0 else cv2.INTER_CUBIC,
        )
        th, tw = candidate.shape[:2]
        if th > phone.shape[0] or tw > phone.shape[1]:
            continue
        result = cv2.matchTemplate(phone, candidate, cv2.TM_CCOEFF_NORMED)
        _, score, _, _ = cv2.minMaxLoc(result)
        best = max(best, float(score))
        if best >= 0.82:
            return True
    return False



def probe_h264_transport(backend) -> dict:
    """Exercise scrcpy H.264 directly, without the PNG fallback."""
    server_path = default_server_path()
    report = {
        "transport": "scrcpy-h264",
        "server_path": str(server_path),
        "server_exists": server_path.is_file(),
        "pass": False,
        "frames": 0,
        "elapsed_seconds": 0.0,
        "fps": 0.0,
        "diagnostics": {},
        "error": "",
    }
    capture = None
    started = time.monotonic()
    try:
        capture = ScrcpyServerCapture(backend, frame_timeout=4.0)
        deadline = started + 8.0
        while time.monotonic() < deadline and report["frames"] < 10:
            frame, _, _ = capture.grab()
            if frame is None or getattr(frame, "size", 0) == 0:
                raise BackendError("scrcpy probe returned an empty frame")
            report["frames"] += 1
        report["pass"] = report["frames"] >= 3
        report["diagnostics"] = capture.diagnostics()
    except Exception as exc:
        report["error"] = str(exc)
        if capture is not None:
            report["diagnostics"] = capture.diagnostics()
    finally:
        if capture is not None:
            capture.close()

    elapsed = max(0.001, time.monotonic() - started)
    report["elapsed_seconds"] = round(elapsed, 3)
    report["fps"] = round(report["frames"] / elapsed, 3)

    debug_dir = Path("debug")
    debug_dir.mkdir(parents=True, exist_ok=True)
    report_path = debug_dir / "preview-h264-probe.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    report["report_path"] = str(report_path.resolve())
    return report


def create_production_capture(backend):
    """Return the exact production capture used by WSA automation."""
    if isinstance(backend, WsaBackend):
        import bot
        return bot.WsaGameWindowCapture()
    return create_preview_capture(backend)


GOOGLE_PACKAGES = {
    "play_services": "com.google.android.gms",
    "play_store": "com.android.vending",
    "framework": "com.google.android.gsf",
}


def close_auxiliary_android_windows(backend) -> dict[str, bool]:
    """Close WSA virtual displays that would contaminate the game framebuffer."""
    closed = {}
    for package in (GOOGLE_PACKAGES["play_store"], "com.android.settings"):
        backend.shell(["am", "force-stop", package], timeout=30)
        closed[package] = True
    return closed


def google_services_smoke(backend, output: str) -> dict:
    """Verify a usable, signed-in GApps runtime without exposing account data."""
    backend.require_ready(native_arm64=False)
    packages = {}
    for label, package in GOOGLE_PACKAGES.items():
        path_output = backend.shell(["pm", "path", package], timeout=30)
        package_dump = backend.shell(["dumpsys", "package", package], timeout=45)
        enabled_output = backend.shell(["pm", "list", "packages", "-e", package], timeout=30)
        version_name = re.search(r"(?m)^\s*versionName=([^\s]+)", package_dump)
        version_code = re.search(r"(?m)^\s*versionCode=(\d+)", package_dump)
        installed = any(line.startswith("package:") for line in path_output.splitlines())
        enabled = f"package:{package}" in enabled_output
        packages[label] = {
            "package": package,
            "installed": installed,
            "enabled": enabled,
            "version_name": version_name.group(1) if version_name else "",
            "version_code": version_code.group(1) if version_code else "",
        }

    required_packages_ok = all(
        entry["installed"] and entry["enabled"] and entry["version_code"]
        for entry in packages.values()
    )

    # AccountManager output includes the account name. Inspect it only in
    # memory and persist a boolean so email addresses and tokens never enter
    # console logs, JSON evidence or the runtime-reports branch.
    try:
        account_output = backend.shell(["cmd", "account", "list"], timeout=30)
    except BackendError:
        account_output = backend.shell(["dumpsys", "account"], timeout=30)
    account_present = bool(re.search(r"(?i)type\s*=\s*com\.google|com\.google", account_output))

    backend.shell(
        [
            "monkey", "-p", GOOGLE_PACKAGES["play_store"],
            "-c", "android.intent.category.LAUNCHER", "1",
        ],
        timeout=30,
    )
    observed_pids = []
    foreground = False
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        pid = backend.shell(["pidof", GOOGLE_PACKAGES["play_store"]], timeout=10).strip()
        if pid and (not observed_pids or observed_pids[-1] != pid):
            observed_pids.append(pid)
        focus = backend.shell(["dumpsys", "window", "windows"], timeout=15)
        foreground = GOOGLE_PACKAGES["play_store"] in focus and bool(pid)
        if foreground:
            break
        time.sleep(1.0)

    crash_loop = len(observed_pids) > 1
    anr_dump = backend.shell(["dumpsys", "activity", "lastanr"], timeout=20)
    play_store_anr = bool(
        GOOGLE_PACKAGES["play_store"] in anr_dump
        and re.search(r"(?i)\bANR\b", anr_dump)
    )

    screenshot = Path(output)
    screenshot.parent.mkdir(parents=True, exist_ok=True)
    # Play Store can be foreground while its first WSA surface is still a
    # black splash frame.  Do not mistake that short transition for a broken
    # GApps runtime, but keep the wait bounded and require a real rendered
    # Android frame before this gate can pass.
    frame_ok = False
    frame_attempts = 0
    frame_stddev = 0.0
    frame_error = ""
    frame_deadline = time.monotonic() + 10.0
    while time.monotonic() < frame_deadline:
        frame_attempts += 1
        try:
            frame = backend.frame()
            if frame is None or frame.size == 0:
                frame_error = "Android screencap returned an empty frame"
            else:
                frame_stddev = float(np.std(frame))
                # Preserve the observed frame even on failure so the current
                # acceptance run has diagnostic evidence rather than relying
                # on a prior run's screenshot.
                screenshot_written = cv2.imwrite(str(screenshot), frame)
                if frame_stddev > 1.0 and screenshot_written:
                    frame_ok = True
                    break
                if not screenshot_written:
                    frame_error = "Could not write Play Store UI evidence"
                else:
                    frame_error = f"Android UI frame is blank (stddev={frame_stddev:.3f})"
        except BackendError as exc:
            frame_error = str(exc)
        time.sleep(0.5)
    auxiliary_cleanup = close_auxiliary_android_windows(backend)
    cleanup_deadline = time.monotonic() + 5.0
    play_store_closed = False
    while time.monotonic() < cleanup_deadline:
        try:
            remaining_pid = backend.shell(
                ["pidof", GOOGLE_PACKAGES["play_store"]], timeout=10
            ).strip()
        except BackendError:
            # Android's pidof exits with status 1 and no output when the
            # process is absent.  Immediately after force-stop that is the
            # successful cleanup state, not a transport failure.
            remaining_pid = ""
        if not remaining_pid:
            play_store_closed = True
            break
        time.sleep(0.5)
    passed = bool(
        required_packages_ok
        and account_present
        and observed_pids
        and foreground
        and not crash_loop
        and not play_store_anr
        and frame_ok
        and play_store_closed
    )
    report = {
        "pass": passed,
        "play_services": bool(packages["play_services"]["installed"] and packages["play_services"]["enabled"]),
        "play_store": bool(packages["play_store"]["installed"] and packages["play_store"]["enabled"]),
        "framework": bool(packages["framework"]["installed"] and packages["framework"]["enabled"]),
        "account_present": account_present,
        "play_store_launch": bool(observed_pids),
        "play_store_foreground": foreground,
        "play_store_crash_loop": crash_loop,
        "play_store_anr": play_store_anr,
        "ui_frame": frame_ok,
        "ui_frame_attempts": frame_attempts,
        "ui_frame_stddev": round(frame_stddev, 3),
        "ui_frame_error": frame_error,
        "play_store_closed_after_evidence": play_store_closed,
        "auxiliary_windows_closed": auxiliary_cleanup,
        "screenshot": str(screenshot.resolve()) if screenshot.is_file() else "",
        "packages": packages,
    }
    report_path = screenshot.with_suffix(".json")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report["report_path"] = str(report_path.resolve())
    return report

def prepare_mvp_flow(backend) -> dict:
    """Prepare exactly one destructive-but-explicit MVP registration cycle."""
    import bot
    from runtime_events import emit_event

    backend.require_ready(native_arm64=isinstance(backend, NativeArm64Backend))
    if not getattr(backend, "package_installed", lambda: False)():
        raise BackendError("Game is not installed; run bootstrap first.")

    old = bot.load_state()
    next_nickname = int(old.get("next_nickname", 1))
    characters_before = int(old.get("characters_created", 0))
    current_cycle = int(old.get("current_cycle", 1))

    backend.stop_app()
    # Google acceptance opens Play Store in a separate WSA host window. Close
    # it before launching Kingshot so the screen-coordinate production capture
    # cannot be occluded by another Android app window.
    auxiliary_cleanup = close_auxiliary_android_windows(backend)
    clear_result = backend.clear_app_data()
    runtime_permissions = backend.grant_runtime_permissions()

    fresh = dict(bot.DEFAULT_STATE)
    fresh["next_nickname"] = next_nickname
    fresh["pending_nickname"] = next_nickname
    fresh["characters_created"] = characters_before
    fresh["characters_created_cycle"] = 0
    fresh["characters_per_cycle"] = 1
    fresh["auto_reset_data"] = True
    fresh["repeat_cycles"] = False
    fresh["current_cycle"] = current_cycle
    fresh["target_state"] = 3
    fresh["tutorial_origin"] = "initial"
    fresh["last_stop_reason"] = ""
    bot.save_state(fresh)

    Path(bot.CONTROL_FILE).write_text(
        json.dumps({"paused": False, "stop": False}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    expected_nickname = f"Тугарин{next_nickname}"
    emit_event(
        "mvp_flow_start",
        expected_nickname=expected_nickname,
        next_nickname_before=next_nickname,
        characters_before=characters_before,
        current_cycle=current_cycle,
    )

    backend.launch_app()
    return {
        "prepared": True,
        "clear_result": str(clear_result).strip(),
        "runtime_permissions": runtime_permissions,
        "auxiliary_windows_closed": auxiliary_cleanup,
        "expected_nickname": expected_nickname,
        "next_nickname_before": next_nickname,
        "characters_before": characters_before,
        "target_state": 3,
        "characters_per_cycle": 1,
        "repeat_cycles": False,
    }


def collect_mvp_flow_evidence() -> dict:
    import bot
    from runtime_events import read_recent_events

    state = bot.load_state()
    events = read_recent_events(limit=5000)
    expected_run_id = os.environ.get("TUGARIN_ACCEPTANCE_RUN_ID", "").strip()
    start_index = -1
    for index, event in enumerate(events):
        if event.get("event") == "mvp_flow_start" and (
            not expected_run_id or event.get("run_id") == expected_run_id
        ):
            start_index = index

    checks = {
        "start_event": start_index >= 0,
        "initial_tutorial_complete": False,
        "state3_confirmed": False,
        "character_tutorial_complete": False,
        "nickname_committed": False,
        "nickname_evidence_screenshot": False,
        "nickname_ocr_confirmed": False,
        "ordered_flow": False,
        "final_phase_complete": state.get("phase") == "complete",
        "final_step_done": state.get("step") == "done",
        "no_stop_reason": not bool(state.get("last_stop_reason")),
        "character_delta_one": False,
        "nickname_counter_advanced": False,
    }
    expected_nickname = ""
    evidence_events = []
    if start_index >= 0:
        flow = [
            event for event in events[start_index:]
            if not expected_run_id or event.get("run_id") == expected_run_id
        ]
        start = flow[0]
        expected_nickname = str(start.get("expected_nickname", ""))
        characters_before = int(start.get("characters_before", 0))
        nickname_before = int(start.get("next_nickname_before", 1))

        positions = {}
        for offset, event in enumerate(flow):
            kind = event.get("event")
            if kind == "tutorial_complete" and event.get("origin") == "initial":
                positions.setdefault("initial", offset)
                checks["initial_tutorial_complete"] = True
            elif kind == "state3_confirmed" and int(event.get("target_state", 0)) == 3:
                positions.setdefault("state3", offset)
                checks["state3_confirmed"] = True
            elif kind == "tutorial_complete" and event.get("origin") == "new_character":
                positions.setdefault("character_tutorial", offset)
                checks["character_tutorial_complete"] = True
            elif kind == "nickname_committed" and event.get("nickname") == expected_nickname:
                positions.setdefault("nickname", offset)
                checks["nickname_committed"] = True
                evidence_path = str(event.get("evidence_screenshot", "") or "")
                checks["nickname_evidence_screenshot"] = bool(
                    evidence_path and Path(evidence_path).is_file()
                )
                checks["nickname_ocr_confirmed"] = (
                    not expected_run_id or bool(event.get("nickname_ocr_confirmed"))
                )

            if kind in {
                "mvp_flow_start",
                "tutorial_complete",
                "state3_confirmed",
                "nickname_committed",
                "cycle_reset",
                "runtime_probe",
                "game_resource_blocked",
            }:
                evidence_events.append(event)

        checks["ordered_flow"] = all(k in positions for k in (
            "initial", "state3", "character_tutorial", "nickname"
        )) and (
            positions["initial"]
            < positions["state3"]
            < positions["character_tutorial"]
            < positions["nickname"]
        )
        checks["character_delta_one"] = (
            int(state.get("characters_created", 0)) == characters_before + 1
        )
        checks["nickname_counter_advanced"] = (
            int(state.get("next_nickname", 0)) == nickname_before + 1
        )

    passed = all(checks.values())
    return {
        "pass": passed,
        "run_id": expected_run_id,
        "head": os.environ.get("TUGARIN_ACCEPTANCE_HEAD", "").strip(),
        "expected_nickname": expected_nickname,
        "checks": checks,
        "state": {
            "phase": state.get("phase"),
            "step": state.get("step"),
            "next_nickname": state.get("next_nickname"),
            "characters_created": state.get("characters_created"),
            "characters_created_cycle": state.get("characters_created_cycle"),
            "last_stop_reason": state.get("last_stop_reason"),
        },
        "evidence_events": evidence_events[-50:],
    }


def _wait_package_stopped(backend, *, timeout: float = 10.0) -> bool:
    """Wait for Android's asynchronous ``am force-stop`` to take effect."""
    deadline = time.monotonic() + max(0.0, float(timeout))
    while True:
        if not backend.health().package_running:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.25)


def _wait_production_frame(backend, *, timeout: float = 30.0) -> bool:
    """Wait until the relaunched game's HWND exists and yields a real frame."""
    deadline = time.monotonic() + max(0.0, float(timeout))
    while True:
        capture = None
        try:
            capture = create_production_capture(backend)
            frame, _, _ = capture.grab()
            if frame is not None and getattr(frame, "size", 0) > 0:
                return True
        except (BackendError, OSError, RuntimeError):
            pass
        finally:
            if capture is not None:
                capture.close()
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.5)


def run_recovery_smoke(backend, *, with_adb_reconnect: bool = False) -> dict:
    """Real-host recovery smoke with no game UI clicks."""
    from runtime_recovery import RecoveryController

    health = backend.require_ready(native_arm64=isinstance(backend, NativeArm64Backend))
    if not getattr(backend, "package_installed", lambda: False)():
        raise BackendError("Kingshot is not installed")
    if not health.package_running:
        backend.launch_app()
        backend.wait_package_running(timeout=90)

    result = {
        "pass": False,
        "baseline_frame": False,
        "game_stop_observed": False,
        "game_restart_action": "",
        "frame_after_restart": False,
        "adb_reconnect_requested": bool(with_adb_reconnect),
        "adb_reconnect": not with_adb_reconnect,
        "frame_after_adb_reconnect": not with_adb_reconnect,
    }
    result["baseline_frame"] = _wait_production_frame(backend)
    if not result["baseline_frame"]:
        result["error"] = "Kingshot HWND did not produce a frame before recovery"
        return result

    backend.stop_app()
    result["game_stop_observed"] = _wait_package_stopped(backend)
    if not result["game_stop_observed"]:
        result["error"] = "Kingshot PID did not disappear after force-stop"
        return result

    controller = RecoveryController(max_game_restarts=1)
    decision = controller.probe_runtime(backend)
    result["game_restart_action"] = decision.action
    if decision.terminal or decision.action != "game_restarted":
        result["error"] = (
            f"Recovery did not restart stopped Kingshot: "
            f"{decision.action} {decision.detail}"
        )
        return result

    result["frame_after_restart"] = _wait_production_frame(backend)

    if with_adb_reconnect:
        adb_path = str(getattr(backend, "adb_path", "") or "")
        if not adb_path:
            raise BackendError("ADB reconnect smoke requires adb_path")
        subprocess.run(
            [adb_path, "disconnect", backend.serial],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        time.sleep(1.0)
        backend.connect()
        deadline = time.monotonic() + 30.0
        ready = False
        while time.monotonic() < deadline:
            if backend.health().ready:
                ready = True
                break
            time.sleep(1.0)
            backend.connect()
        result["adb_reconnect"] = ready
        if not ready:
            raise BackendError("ADB did not recover within 30 seconds")

        result["frame_after_adb_reconnect"] = _wait_production_frame(backend)

    result["pass"] = all((
        result["baseline_frame"],
        result["game_stop_observed"],
        result["game_restart_action"] == "game_restarted",
        result["frame_after_restart"],
        result["adb_reconnect"],
        result["frame_after_adb_reconnect"],
    ))
    return result


def prepare_mvp_soak(backend) -> dict:
    """Prepare repeated one-character cycles so reset/re-entry are exercised."""
    import bot
    from runtime_events import emit_event

    backend.require_ready(native_arm64=isinstance(backend, NativeArm64Backend))
    if not getattr(backend, "package_installed", lambda: False)():
        raise BackendError("Game is not installed; run bootstrap first.")

    old = bot.load_state()
    next_nickname = int(old.get("next_nickname", 1))
    characters_before = int(old.get("characters_created", 0))
    current_cycle = int(old.get("current_cycle", 1))

    backend.stop_app()
    auxiliary_cleanup = close_auxiliary_android_windows(backend)
    clear_result = backend.clear_app_data()
    runtime_permissions = backend.grant_runtime_permissions()

    fresh = dict(bot.DEFAULT_STATE)
    fresh["next_nickname"] = next_nickname
    fresh["pending_nickname"] = next_nickname
    fresh["characters_created"] = characters_before
    fresh["characters_created_cycle"] = 0
    fresh["characters_per_cycle"] = 1
    fresh["auto_reset_data"] = True
    fresh["repeat_cycles"] = True
    fresh["current_cycle"] = current_cycle
    fresh["target_state"] = 3
    fresh["tutorial_origin"] = "initial"
    fresh["last_stop_reason"] = ""
    bot.save_state(fresh)
    Path(bot.CONTROL_FILE).write_text(
        json.dumps({"paused": False, "stop": False}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    emit_event(
        "mvp_soak_start",
        next_nickname_before=next_nickname,
        characters_before=characters_before,
        current_cycle=current_cycle,
    )
    backend.launch_app()
    return {
        "prepared": True,
        "clear_result": str(clear_result).strip(),
        "runtime_permissions": runtime_permissions,
        "auxiliary_windows_closed": auxiliary_cleanup,
        "next_nickname_before": next_nickname,
        "characters_before": characters_before,
        "characters_per_cycle": 1,
        "repeat_cycles": True,
    }


def collect_mvp_soak_evidence(min_characters: int = 2) -> dict:
    import bot
    from runtime_events import read_recent_events

    minimum = max(2, int(min_characters))
    state = bot.load_state()
    events = read_recent_events(limit=10000)
    expected_run_id = os.environ.get("TUGARIN_ACCEPTANCE_RUN_ID", "").strip()
    start_index = -1
    for index, event in enumerate(events):
        if event.get("event") == "mvp_soak_start" and (
            not expected_run_id or event.get("run_id") == expected_run_id
        ):
            start_index = index

    result = {
        "pass": False,
        "run_id": expected_run_id,
        "head": os.environ.get("TUGARIN_ACCEPTANCE_HEAD", "").strip(),
        "min_characters": minimum,
        "characters_delta": 0,
        "nickname_commits": [],
        "nickname_evidence_screenshots": [],
        "cycle_resets": 0,
        "ordered_nicknames": False,
        "no_stop_reason": not bool(state.get("last_stop_reason")),
        "state": {
            "phase": state.get("phase"),
            "step": state.get("step"),
            "next_nickname": state.get("next_nickname"),
            "characters_created": state.get("characters_created"),
            "current_cycle": state.get("current_cycle"),
            "last_stop_reason": state.get("last_stop_reason"),
        },
    }
    if start_index < 0:
        result["error"] = "mvp_soak_start event not found"
        return result

    flow = [
        event for event in events[start_index:]
        if not expected_run_id or event.get("run_id") == expected_run_id
    ]
    start = flow[0]
    before = int(start.get("characters_before", 0))
    nickname_before = int(start.get("next_nickname_before", 1))
    commit_events = [
        e for e in flow if e.get("event") == "nickname_committed"
    ]
    commits = [str(e.get("nickname")) for e in commit_events]
    screenshots = [
        str(e.get("evidence_screenshot", "") or "")
        for e in commit_events
    ]
    valid_screenshots = [
        path for path in screenshots if path and Path(path).is_file()
    ]
    ocr_confirmed = [
        bool(e.get("nickname_ocr_confirmed")) for e in commit_events
    ]
    resets = [e for e in flow if e.get("event") == "cycle_reset"]
    delta = int(state.get("characters_created", 0)) - before
    expected = [f"Тугарин{nickname_before + i}" for i in range(minimum)]

    result["characters_delta"] = delta
    result["nickname_commits"] = commits
    result["nickname_evidence_screenshots"] = valid_screenshots
    result["cycle_resets"] = len(resets)
    result["ordered_nicknames"] = commits[:minimum] == expected
    result["pass"] = all((
        delta >= minimum,
        len(commits) >= minimum,
        len(valid_screenshots) >= minimum,
        (not expected_run_id or sum(ocr_confirmed[:minimum]) == minimum),
        len(resets) >= minimum - 1,
        result["ordered_nicknames"],
        result["no_stop_reason"],
    ))
    return result


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    backend = create_backend(
        args.backend,
        serial=args.serial,
        adb_path=args.adb,
    )

    if args.action == "status":
        print(json.dumps(backend.health().to_dict(), ensure_ascii=False, indent=2))
        return 0

    if args.action == "resource-diagnostics":
        # Non-destructive diagnostic path: no game reset, UI input or guessed
        # private game-CDN probes. Exit zero means diagnostic written, NOT that
        # the Kingshot resource service is working.
        from resource_diagnostics import (
            collect_resource_network_diagnostics, save_resource_network_diagnostics,
        )
        report = collect_resource_network_diagnostics(
            backend,
            run_id=os.environ.get("TUGARIN_ACCEPTANCE_RUN_ID", ""),
            head=os.environ.get("TUGARIN_ACCEPTANCE_HEAD", ""),
        )
        output = Path("debug") / "game-resource-network-diagnostics.json"
        save_resource_network_diagnostics(output, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    if args.action == "resource-readiness":
        # Two read-only game UI observations are required before destructive
        # acceptance. A successful network probe alone is never enough.
        from resource_readiness import probe_resource_readiness
        from resource_diagnostics import save_resource_network_diagnostics
        report = probe_resource_readiness(
            backend,
            run_id=os.environ.get("TUGARIN_ACCEPTANCE_RUN_ID", ""),
            head=os.environ.get("TUGARIN_ACCEPTANCE_HEAD", ""),
        )
        save_resource_network_diagnostics(
            Path("debug") / "resource-readiness.json", report,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["pass"] else 51

    if args.action == "restriction-check":
        backend.require_ready(native_arm64=isinstance(backend, NativeArm64Backend))
        import bot
        frame = backend.frame()
        phone, _, _ = bot.crop_phone(frame)
        reason = bot.detect_stop_reason(phone)
        evidence = ""
        if reason:
            output = Path(args.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            if not cv2.imwrite(str(output), phone):
                raise BackendError(f"Could not save restriction evidence: {output}")
            evidence = str(output.resolve())
        print(json.dumps({
            "pass": not bool(reason),
            "restricted": bool(reason),
            "reason": reason,
            "evidence_screenshot": evidence,
        }, ensure_ascii=False, indent=2))
        return 4 if reason else 0

    if args.action == "start-runtime":
        if not isinstance(backend, (NativeArm64Backend, WsaBackend)):
            raise BackendError("start-runtime requires --backend native_arm64 or wsa")
        backend.start_runtime(wipe=args.wipe, window=args.window)
        return 0

    if args.action == "stop-runtime":
        if not isinstance(backend, NativeArm64Backend):
            raise BackendError(
                "stop-runtime is supported only for --backend native_arm64; "
                "WSA lifecycle is managed by Windows."
            )
        backend.stop_runtime()
        return 0

    if args.action == "preview-probe":
        report = probe_h264_transport(backend)
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
        return 0 if report["pass"] else 3

    if args.action == "google-services-smoke":
        report = google_services_smoke(backend, args.output)
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
        return 0 if report["pass"] else 5

    if args.action == "preview-smoke":
        backend.require_ready(native_arm64=isinstance(backend, NativeArm64Backend))
        capture = None
        if isinstance(backend, WsaBackend) and args.require_fast:
            capture = create_production_capture(backend)
        elif isinstance(backend, WsaBackend):
            try:
                capture = create_production_capture(backend)
            except Exception:
                capture = None
        if capture is None:
            capture = create_preview_capture(backend)

        duration = max(2.0, float(args.preview_seconds))
        started = time.monotonic()
        deadline = started + duration
        frames = 0
        latencies = []
        transports = []
        last_shape = None
        last_frame = None
        last_title = ""
        last_rect = None
        black_frames = 0
        frozen_frames = 0
        capture_errors = 0
        previous_frame = None
        source_start = None
        if args.require_fast and isinstance(backend, WsaBackend):
            source_start = backend.frame()
        try:
            while time.monotonic() < deadline:
                before = time.monotonic()
                try:
                    frame, title, rect = capture.grab()
                except Exception:
                    capture_errors += 1
                    if (
                        frames == 0
                        and isinstance(backend, WsaBackend)
                        and not args.require_fast
                    ):
                        capture.close()
                        capture = create_preview_capture(backend)
                        frame, title, rect = capture.grab()
                    else:
                        raise
                elapsed_ms = (time.monotonic() - before) * 1000.0
                if frame is None or getattr(frame, "size", 0) == 0:
                    raise BackendError("Preview returned an empty frame")
                frames += 1
                if float(frame.mean()) < 2.0 or float(np.count_nonzero(frame)) / float(frame.size) < 0.01:
                    black_frames += 1
                if previous_frame is not None and frame.shape == previous_frame.shape and np.array_equal(frame, previous_frame):
                    frozen_frames += 1
                previous_frame = frame.copy()
                last_frame = frame.copy()
                last_title = str(title)
                last_rect = dict(rect or {})
                latencies.append(elapsed_ms)
                transport = str(getattr(capture, "transport_name", "unknown"))
                if not transports or transports[-1] != transport:
                    transports.append(transport)
                last_shape = list(frame.shape)
        finally:
            capture.close()

        elapsed = max(0.001, time.monotonic() - started)
        source_changed = False
        if source_start is not None:
            source_end = backend.frame()
            if source_end is not None and source_end.shape == source_start.shape:
                source_changed = not np.array_equal(source_start, source_end)
        ordered = sorted(latencies)
        p95_latency = None
        avg_latency = None
        if ordered:
            p95_index = min(len(ordered)-1, max(0, int(round((len(ordered)-1)*0.95))))
            p95_latency = round(ordered[p95_index], 3)
            avg_latency = round(sum(latencies) / len(latencies), 3)

        active_transport = transports[-1] if transports else "unknown"
        capture_method = str(getattr(capture, "capture_method", "") or "")
        printwindow_transport = (
            active_transport == "wsa-window" and capture_method == "printwindow"
        )
        fast_transport = active_transport in {"wsa-window", "scrcpy-h264"}
        fps = frames / elapsed
        window_changed = frozen_frames < max(3, frames - 1)
        stale_stream = bool(source_changed and not window_changed)
        visual_ok = black_frames == 0 and not stale_stream
        passed = frames >= 3
        if args.require_fast:
            passed = bool(
                passed and fast_transport and fps >= float(args.min_preview_fps)
                and visual_ok and capture_errors == 0
            )
        if args.require_printwindow:
            passed = bool(passed and printwindow_transport)

        result = {
            "pass": passed,
            "seconds": round(elapsed, 3),
            "frames": frames,
            "fps": round(fps, 3),
            "avg_latency_ms": avg_latency,
            "p95_latency_ms": p95_latency,
            "active_transport": active_transport,
            "capture_method": capture_method,
            "printwindow_transport": printwindow_transport,
            "hwnd_targeted_capture": printwindow_transport,
            "transport_history": transports,
            "frame_shape": last_shape,
            "window_title": last_title,
            "window_rect": last_rect,
            "h264": active_transport in {"scrcpy-h264", "h264-screenrecord"},
            "fast_transport": fast_transport,
            "black_frames": black_frames,
            "frozen_frames": frozen_frames,
            "capture_errors": capture_errors,
            "source_changed": source_changed,
            "window_changed": window_changed,
            "stale_stream": stale_stream,
            "screenshot": "",
            "report_path": "",
        }

        if args.report:
            screenshot = Path(args.output)
            report_path = Path(args.report)
            screenshot.parent.mkdir(parents=True, exist_ok=True)
            report_path.parent.mkdir(parents=True, exist_ok=True)
            if last_frame is not None and cv2.imwrite(str(screenshot), last_frame):
                result["screenshot"] = str(screenshot.resolve())
            result["report_path"] = str(report_path.resolve())
            report_path.write_text(
                json.dumps(result, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        if frames < 3:
            raise BackendError(f"Preview smoke produced too few frames: {frames}")
        if args.require_h264 and not result["h264"]:
            raise BackendError(
                "H.264 preview was required but preview fell back to "
                f"{active_transport}. See JSON metrics above."
            )
        if args.require_printwindow and not printwindow_transport:
            raise BackendError(
                "Release preview requires the exact WSA PrintWindow transport; "
                f"got transport={active_transport!r} method={capture_method!r}."
            )
        if args.require_fast and not result["pass"]:
            raise BackendError(
                "Production preview gate failed: transport="
                f"{active_transport} method={capture_method} fps={result['fps']} "
                f"black={black_frames} frozen={frozen_frames} "
                f"stale={stale_stream} errors={capture_errors}."
            )
        return 0

    if args.action == "screenshot":
        frame = backend.frame()
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(output), frame):
            raise BackendError(f"Could not save screenshot: {output}")
        print(output.resolve())
        return 0

    if args.action == "bootstrap":
        if not isinstance(backend, (NativeArm64Backend, WsaBackend)):
            raise BackendError("bootstrap requires --backend native_arm64 or wsa")

        health = backend.health()
        if not health.ready:
            if isinstance(backend, NativeArm64Backend):
                # A failed pre-ADB guest can leave QEMU alive. Stop only the
                # WAR BOT runtime identified by its pid file before retrying.
                try:
                    backend.stop_runtime()
                except Exception:
                    pass
                backend.start_runtime(wipe=args.wipe, window=args.window)
            else:
                backend.start_runtime(wipe=False, window=True)
                try:
                    health = backend.wait_ready(timeout=180)
                except BackendError as exc:
                    raise BackendError(
                        "WSA is installed but ADB is not ready. Open Windows "
                        "Subsystem for Android -> Advanced settings, enable "
                        "Developer mode, then retry. Default endpoint is "
                        "127.0.0.1:58526."
                    ) from exc

        native_gate = isinstance(backend, NativeArm64Backend)
        health = backend.require_ready(native_arm64=native_gate)

        installed_now = False
        if not backend.package_installed():
            backend.install_verified_game(args.apks_dir)
            installed_now = True

        if args.clean_game:
            backend.clear_app_data()
            backend.grant_runtime_permissions()
            reset_workflow_for_clean_game()

        backend.launch_app()
        pid = backend.wait_package_running(timeout=180)

        # A freshly migrated WSA can have no active Android surface until the
        # first application is launched.  Package-manager readiness is already
        # part of require_ready()/health; install and launch the verified APK
        # first, then require the real framebuffer/network/audio gates before
        # accepting the running game.
        if hasattr(backend, "wait_runtime_services"):
            health = backend.wait_runtime_services(timeout=90)

        stability = max(1, int(args.game_stability_seconds))
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)

        startup_frame = backend.frame()
        startup_output = output.with_name(output.stem + "-startup" + output.suffix)
        if not cv2.imwrite(str(startup_output), startup_frame):
            raise BackendError(f"Could not save startup screenshot: {startup_output}")
        startup_loading = loading_logo_visible(startup_frame)

        print(
            f"Game process {pid} started; verifying stability for {stability}s...",
            flush=True,
        )
        try:
            backend.wait_package_stable(stability, expected_pid=pid)
        except BackendError as exc:
            try:
                crash_path = backend.collect_game_crash()
                crash_detail = str(crash_path)
            except Exception as crash_exc:
                crash_detail = f"collection failed: {crash_exc}"
            raise BackendError(
                f"{exc}; game crash log: {crash_detail}"
            ) from exc

        frame = backend.frame()
        final_loading = loading_logo_visible(frame)
        if not cv2.imwrite(str(output), frame):
            raise BackendError(f"Could not save bootstrap screenshot: {output}")
        if final_loading:
            crash_path = backend.collect_game_crash()
            raise BackendError(
                "Kingshot process stayed alive but remained on the verified loading screen "
                f"after {stability}s; diagnostic log: {crash_path}"
            )

        print(
            f"Game stability/loading gate passed: PID {pid}; "
            f"startup_loading={startup_loading} final_loading={final_loading}",
            flush=True,
        )

        print(json.dumps({
            "ready": True,
            "backend": health.backend,
            "native_arm64": health.native_arm64,
            "native_bridge": health.native_bridge,
            "serial": health.serial,
            "android": health.android,
            "abi": health.abi,
            "model": health.model,
            "resolution": health.resolution,
            "network_ready": health.network_ready,
            "internet_reachable": health.internet_reachable,
            "audio_service_ready": health.audio_service_ready,
            "package_manager_ready": health.package_manager_ready,
            "data_free_mb": health.data_free_mb,
            "installed_now": installed_now,
            "clean_game": bool(args.clean_game),
            "game_pid": pid,
            "game_stability_seconds": stability,
            "startup_loading_logo": startup_loading,
            "final_loading_logo": final_loading,
            "startup_screenshot": str(startup_output.resolve()),
            "screenshot": str(output.resolve()),
        }, ensure_ascii=False, indent=2))
        return 0

    if args.action == "install-game":
        if isinstance(backend, (NativeArm64Backend, WsaBackend)):
            print(backend.install_verified_game(args.apks_dir))
        else:
            raise BackendError(
                "install-game requires --backend native_arm64 or wsa."
            )
        return 0

    if args.action == "launch-game":
        backend.require_ready(native_arm64=isinstance(backend, NativeArm64Backend))
        print(backend.launch_app().strip())
        return 0

    if args.action == "stop-game":
        print(backend.stop_app().strip())
        return 0

    if args.action == "restart-game":
        backend.require_ready(native_arm64=isinstance(backend, NativeArm64Backend))
        backend.stop_app()
        backend.launch_app()
        pid = backend.wait_package_running(timeout=90)
        print(json.dumps({"restarted": True, "game_pid": pid}, ensure_ascii=False))
        return 0

    if args.action == "prepare-mvp-soak":
        if not args.yes:
            raise BackendError("Refusing MVP soak preparation without --yes")
        result = prepare_mvp_soak(backend)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.action == "soak-evidence":
        result = collect_mvp_soak_evidence(args.min_characters)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["pass"] else 6

    if args.action == "operator-io-smoke":
        health = backend.require_ready(
            native_arm64=isinstance(backend, NativeArm64Backend)
        )
        clipboard = backend.unicode_clipboard_health("ТугаринMVP")
        hierarchy = backend.ui_dump()
        result = {
            "pass": bool(
                health.ready
                and health.audio_service_ready
                and clipboard.get("ready")
                and bool(hierarchy.strip())
            ),
            "backend": health.backend,
            "serial": health.serial,
            "audio_service_ready": health.audio_service_ready,
            "unicode_clipboard": clipboard,
            "ui_hierarchy_bytes": len(hierarchy.encode("utf-8")),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["pass"] else 7

    if args.action == "recovery-smoke":
        result = run_recovery_smoke(
            backend,
            with_adb_reconnect=bool(args.with_adb_reconnect),
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["pass"] else 5

    if args.action == "prepare-mvp-flow":
        if not args.yes:
            raise BackendError("Refusing MVP flow preparation without --yes")
        result = prepare_mvp_flow(backend)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.action == "flow-evidence":
        result = collect_mvp_flow_evidence()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["pass"] else 4

    if args.action == "clear-game-data":
        if not args.yes:
            raise BackendError("Refusing pm clear without --yes")
        print(backend.clear_app_data().strip())
        return 0

    if args.action == "clean-start":
        if not args.yes:
            raise BackendError("Refusing clean start without --yes")
        backend.require_ready(native_arm64=isinstance(backend, NativeArm64Backend))
        if not getattr(backend, "package_installed", lambda: False)():
            raise BackendError("Game is not installed; run bootstrap/install-game first.")

        backend.stop_app()
        clear_result = backend.clear_app_data()

        fresh = reset_workflow_for_clean_game()
        backend.launch_app()
        print(json.dumps({
            "clear_result": clear_result.strip(),
            "next_nickname": fresh["next_nickname"],
            "characters_created": fresh["characters_created"],
            "current_cycle": fresh["current_cycle"],
            "phase": fresh["phase"],
            "step": fresh["step"],
        }, ensure_ascii=False, indent=2))
        return 0

    if args.action == "tap":
        if len(args.coords) != 2:
            raise BackendError("tap requires: X Y")
        backend.tap(*args.coords)
        return 0

    if args.action == "swipe":
        if len(args.coords) != 5:
            raise BackendError("swipe requires: X1 Y1 X2 Y2 DURATION_MS")
        backend.swipe(*args.coords)
        return 0

    if args.action == "ui-dump":
        if not hasattr(backend, "ui_dump"):
            raise BackendError("Selected backend has no UI hierarchy channel")
        print(backend.ui_dump())
        return 0

    raise BackendError(f"Unhandled action: {args.action}")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (BackendError, OSError, RuntimeError) as exc:
        print(f"TUGARIN BOTS CLI ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
