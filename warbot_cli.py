"""Agent-friendly CLI for the unified WAR BOT Android backend."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import cv2

from device_backend import BackendError, NativeArm64Backend, create_backend


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="WAR BOT Android backend CLI")
    parser.add_argument(
        "action",
        choices=(
            "status", "screenshot", "bootstrap", "install-game", "launch-game", "stop-game",
            "clear-game-data", "clean-start", "tap", "swipe", "ui-dump",
            "start-runtime", "stop-runtime",
        ),
    )
    parser.add_argument("--backend", default="native_arm64")
    parser.add_argument("--serial", default=None)
    parser.add_argument("--adb", default=None)
    parser.add_argument("--output", default="warbot-frame.png")
    parser.add_argument("--apks-dir", default=None)
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
        if not isinstance(backend, NativeArm64Backend):
            raise BackendError("start-runtime requires --backend native_arm64")
        backend.start_runtime(wipe=args.wipe, window=args.window)
        return 0

    if args.action == "stop-runtime":
        if not isinstance(backend, NativeArm64Backend):
            raise BackendError("stop-runtime requires --backend native_arm64")
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
        if not isinstance(backend, NativeArm64Backend):
            raise BackendError("bootstrap requires --backend native_arm64")

        health = backend.health()
        if not health.ready:
            # A failed pre-ADB guest can leave QEMU alive. Stop only the
            # WAR BOT runtime identified by its pid file before retrying.
            try:
                backend.stop_runtime()
            except Exception:
                pass
            backend.start_runtime(wipe=args.wipe, window=args.window)

        health = backend.require_ready(native_arm64=True)
        installed_now = False
        if not backend.package_installed():
            backend.install_verified_game(args.apks_dir)
            installed_now = True

        if args.clean_game:
            backend.clear_app_data()

        backend.launch_app()
        pid = backend.wait_package_running(timeout=90)
        frame = backend.frame()
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(output), frame):
            raise BackendError(f"Could not save bootstrap screenshot: {output}")

        print(json.dumps({
            "ready": True,
            "native_arm64": health.native_arm64,
            "serial": health.serial,
            "android": health.android,
            "abi": health.abi,
            "installed_now": installed_now,
            "clean_game": bool(args.clean_game),
            "game_pid": pid,
            "screenshot": str(output.resolve()),
        }, ensure_ascii=False, indent=2))
        return 0

    if args.action == "install-game":
        if isinstance(backend, NativeArm64Backend):
            print(backend.install_verified_game(args.apks_dir))
        else:
            raise BackendError(
                "install-game currently requires --backend native_arm64 "
                "so the ARM64 gate is enforced."
            )
        return 0

    if args.action == "launch-game":
        backend.require_ready(native_arm64=isinstance(backend, NativeArm64Backend))
        print(backend.launch_app().strip())
        return 0

    if args.action == "stop-game":
        print(backend.stop_app().strip())
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

        # Keep PC-side nickname history/config but reset only the workflow that
        # belongs to the freshly-cleared Android app.
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
        print(f"WAR BOT CLI ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
