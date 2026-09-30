"""Agent-friendly CLI for the unified WAR BOT Android backend."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import cv2

from device_backend import BackendError, NativeArm64Backend, WsaBackend, create_backend


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TUGARIN BOTS Android backend CLI")
    parser.add_argument(
        "action",
        choices=(
            "status", "screenshot", "bootstrap", "install-game", "launch-game", "stop-game",
            "restart-game",
            "clear-game-data", "clean-start", "tap", "swipe", "ui-dump",
            "start-runtime", "stop-runtime",
        ),
    )
    parser.add_argument("--backend", default="wsa")
    parser.add_argument("--serial", default=None)
    parser.add_argument("--adb", default=None)
    parser.add_argument("--output", default="warbot-frame.png")
    parser.add_argument("--apks-dir", default=None)
    parser.add_argument("--game-stability-seconds", type=int, default=45)
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

        # P0 runtime acceptance: do not install/launch the game until Android
        # has a real framebuffer, validated networking and an audio service.
        if hasattr(backend, "wait_runtime_services"):
            health = backend.wait_runtime_services(timeout=90)

        installed_now = False
        if not backend.package_installed():
            backend.install_verified_game(args.apks_dir)
            installed_now = True

        if args.clean_game:
            backend.clear_app_data()
            reset_workflow_for_clean_game()

        backend.launch_app()
        pid = backend.wait_package_running(timeout=180)
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
