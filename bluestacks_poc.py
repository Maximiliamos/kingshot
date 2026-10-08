"""BlueStacks 5 compatibility PoC for WAR BOT.

This probe continues after the official Android Emulator G2 failure.
It does not patch APKs, native libraries, Play Integrity, or emulator detection.

Expected flow:
1. Create a BlueStacks Android 11 64-bit instance with ARM64 (or ARM+x86) ABI.
2. Install com.got.globalru from Google Play inside that instance.
3. Enable ADB in BlueStacks Settings -> Advanced.
4. Use this CLI to discover the instance, connect ADB, inspect the delivered
   package/splits/ABI, launch the game, capture a frame, and collect evidence.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
from pathlib import Path


PACKAGE = "com.got.globalru"
ACTIVITY = "com.unity3d.player.MyMainPlayerActivity"

INSTALL_DIR = Path(os.environ.get("WAR_BOT_BS_INSTALL", r"C:\Program Files\BlueStacks_nxt"))
CONFIG_FILE = Path(os.environ.get("WAR_BOT_BS_CONFIG", r"C:\ProgramData\BlueStacks_nxt\bluestacks.conf"))
ANDROID_SDK = Path(os.environ.get("WAR_BOT_ANDROID_SDK", r"C:\Android\Sdk"))
EVIDENCE_DIR = Path(os.environ.get("WAR_BOT_BS_EVIDENCE", r"C:\warbot_bluestacks_poc"))

PLAYER = INSTALL_DIR / "HD-Player.exe"
ADB_CANDIDATES = (
    ANDROID_SDK / "platform-tools" / "adb.exe",
    INSTALL_DIR / "HD-Adb.exe",
    INSTALL_DIR / "adb.exe",
)


def run(args, *, timeout=120, check=True, text=True):
    result = subprocess.run(
        [str(v) for v in args],
        capture_output=True,
        text=text,
        encoding="utf-8" if text else None,
        errors="replace" if text else None,
        timeout=timeout,
        check=False,
    )
    if check and result.returncode:
        if text:
            detail = (result.stderr or result.stdout or "unknown error").strip()
        else:
            detail = f"exit={result.returncode}"
        raise RuntimeError(f"Command failed ({result.returncode}): {detail}")
    return result


def find_adb() -> Path:
    for path in ADB_CANDIDATES:
        if path.is_file():
            return path
    raise RuntimeError(
        "ADB not found. Expected Android Platform Tools or BlueStacks ADB under "
        f"{INSTALL_DIR}"
    )


def parse_config(path: Path = CONFIG_FILE) -> dict[str, str]:
    if not path.is_file():
        raise RuntimeError(f"BlueStacks config not found: {path}")
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] == '"':
            value = value[1:-1]
        values[key.strip()] = value
    return values


def list_instances(config: dict[str, str] | None = None) -> list[dict[str, str]]:
    config = config or parse_config()
    names: set[str] = set()
    pattern = re.compile(r"^bst\.instance\.([^.]+)\.")
    for key in config:
        match = pattern.match(key)
        if match:
            names.add(match.group(1))

    result = []
    for name in sorted(names):
        prefix = f"bst.instance.{name}."
        adb_port = (
            config.get(prefix + "status.adb_port")
            or config.get(prefix + "adb_port")
            or ""
        )
        result.append({
            "name": name,
            "display_name": config.get(prefix + "display_name", name),
            "abi_list": config.get(prefix + "abi_list", ""),
            "android_image": config.get(prefix + "android_image", ""),
            "adb_port": adb_port,
        })
    return result


def choose_instance(requested: str | None = None) -> dict[str, str]:
    instances = list_instances()
    if not instances:
        raise RuntimeError("No BlueStacks instances were found")

    requested = requested or os.environ.get("WAR_BOT_BS_INSTANCE")
    if requested:
        for item in instances:
            if requested in (item["name"], item["display_name"]):
                return item
        raise RuntimeError(f"BlueStacks instance not found: {requested}")

    # Prefer Android 11 instances (Rvc64), then any 64-bit instance.
    for item in instances:
        if item["name"].startswith("Rvc64"):
            return item
    for item in instances:
        if item["name"].endswith("64") or "64" in item["abi_list"]:
            return item
    return instances[0]


def start_instance(instance: dict[str, str]) -> None:
    if not PLAYER.is_file():
        raise RuntimeError(f"BlueStacks player not found: {PLAYER}")
    subprocess.Popen(
        [str(PLAYER), "--instance", instance["name"]],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
    )
    print(f"BlueStacks instance launch requested: {instance['name']}")


def serial_for(instance: dict[str, str]) -> str:
    override = os.environ.get("WAR_BOT_BS_SERIAL")
    if override:
        return override
    port = os.environ.get("WAR_BOT_BS_ADB_PORT") or instance.get("adb_port", "")
    if not port:
        raise RuntimeError(
            "ADB port not found in bluestacks.conf. Enable Android Debug Bridge "
            "in BlueStacks Settings -> Advanced, then retry or set WAR_BOT_BS_ADB_PORT."
        )
    return f"127.0.0.1:{port}"


def adb_connect(instance: dict[str, str]) -> str:
    adb = find_adb()
    serial = serial_for(instance)
    result = run([adb, "connect", serial], check=False)
    message = (result.stdout or result.stderr).strip()
    if "connected" not in message.lower() and "already connected" not in message.lower():
        raise RuntimeError(f"ADB connect failed for {serial}: {message}")
    return serial


def adb(instance: dict[str, str], *args, timeout=60, check=True):
    executable = find_adb()
    serial = serial_for(instance)
    return run([executable, "-s", serial, *args], timeout=timeout, check=check)


def wait_for_device(instance: dict[str, str], timeout=180) -> None:
    serial = adb_connect(instance)
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        state = run([find_adb(), "-s", serial, "get-state"], check=False)
        last = state.stdout.strip()
        if last == "device":
            boot = adb(instance, "shell", "getprop", "sys.boot_completed", check=False).stdout.strip()
            if boot == "1":
                print(f"ADB ready: {serial}")
                return
        time.sleep(2)
    raise TimeoutError(f"BlueStacks ADB did not become ready; last_state={last!r}")


def prop(instance: dict[str, str], name: str) -> str:
    return adb(instance, "shell", "getprop", name, check=False).stdout.strip()


def package_installed(instance: dict[str, str]) -> bool:
    result = adb(instance, "shell", "pm", "path", PACKAGE, check=False)
    return result.returncode == 0 and "package:" in result.stdout


def package_paths(instance: dict[str, str]) -> list[str]:
    result = adb(instance, "shell", "pm", "path", PACKAGE, check=False)
    return [
        line.removeprefix("package:").strip()
        for line in result.stdout.splitlines()
        if line.startswith("package:")
    ]


def package_summary(instance: dict[str, str]) -> dict[str, object]:
    dump = adb(instance, "shell", "dumpsys", "package", PACKAGE, check=False).stdout
    version_name = re.search(r"versionName=([^\s]+)", dump)
    version_code = re.search(r"versionCode=(\d+)", dump)
    primary_abi = re.search(r"primaryCpuAbi=([^\s]+)", dump)
    return {
        "installed": "versionName=" in dump,
        "version_name": version_name.group(1) if version_name else "",
        "version_code": int(version_code.group(1)) if version_code else None,
        "primary_cpu_abi": primary_abi.group(1) if primary_abi else "",
        "paths": package_paths(instance),
    }


def diagnostics(instance: dict[str, str]) -> dict[str, object]:
    wait_for_device(instance)
    info = {
        "instance": instance,
        "serial": serial_for(instance),
        "android_release": prop(instance, "ro.build.version.release"),
        "android_sdk": prop(instance, "ro.build.version.sdk"),
        "abi": prop(instance, "ro.product.cpu.abilist"),
        "abi64": prop(instance, "ro.product.cpu.abilist64"),
        "native_bridge": prop(instance, "ro.dalvik.vm.native.bridge"),
        "model": prop(instance, "ro.product.model"),
        "manufacturer": prop(instance, "ro.product.manufacturer"),
        "brand": prop(instance, "ro.product.brand"),
        "device": prop(instance, "ro.product.device"),
        "fingerprint": prop(instance, "ro.build.fingerprint"),
        "resolution": adb(instance, "shell", "wm", "size", check=False).stdout.strip(),
        "density": adb(instance, "shell", "wm", "density", check=False).stdout.strip(),
        "package": package_summary(instance),
    }
    return info


def launch_game(instance: dict[str, str]) -> None:
    if not package_installed(instance):
        raise RuntimeError(
            f"{PACKAGE} is not installed. Install the game from Google Play inside "
            "this BlueStacks instance before continuing."
        )
    result = adb(
        instance, "shell", "am", "start", "-W", "-n", f"{PACKAGE}/{ACTIVITY}",
        timeout=120,
    )
    print(result.stdout.strip())


def collect_crash_evidence(instance: dict[str, str], prefix="g2") -> Path:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    evidence = EVIDENCE_DIR / f"{prefix}_{stamp}.txt"
    dropbox = adb(
        instance, "shell", "dumpsys", "dropbox", "--print", "data_app_crash",
        check=False, timeout=120,
    ).stdout
    logcat = adb(
        instance, "logcat", "-d", "-v", "threadtime",
        check=False, timeout=120,
    ).stdout
    needles = (
        PACKAGE, "AndroidRuntime", "FATAL EXCEPTION", "Fatal signal",
        "UnsatisfiedLinkError", "Unity", "linker", "libndk_translation",
        "libnesec", "PlayIntegrity", "Integrity",
    )
    filtered = "\n".join(
        line for line in logcat.splitlines()
        if any(needle.lower() in line.lower() for needle in needles)
    )
    evidence.write_text(
        "=== DROPBOX data_app_crash ===\n"
        + dropbox
        + "\n\n=== FILTERED LOGCAT ===\n"
        + filtered,
        encoding="utf-8",
    )
    return evidence


def verify_game(instance: dict[str, str], wait_seconds=30) -> bool:
    launch_game(instance)
    time.sleep(wait_seconds)
    pid = adb(instance, "shell", "pidof", PACKAGE, check=False).stdout.strip()
    if pid:
        print(f"G2 PASS: {PACKAGE} is alive as PID {pid}")
        return True
    evidence = collect_crash_evidence(instance)
    raise RuntimeError(f"G2 FAIL: game exited after launch; evidence={evidence}")


def capture(instance: dict[str, str]) -> Path:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    output = EVIDENCE_DIR / f"frame_{time.strftime('%Y%m%d_%H%M%S')}.png"
    result = subprocess.run(
        [str(find_adb()), "-s", serial_for(instance), "exec-out", "screencap", "-p"],
        capture_output=True,
        timeout=60,
        check=False,
    )
    if result.returncode or not result.stdout.startswith(b"\x89PNG"):
        raise RuntimeError("ADB screencap did not return a PNG frame")
    output.write_bytes(result.stdout)
    print(output)
    return output


def print_json(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="WAR BOT BlueStacks 5 compatibility PoC")
    parser.add_argument(
        "action",
        choices=("instances", "start", "connect", "status", "launch", "verify", "capture", "all"),
    )
    parser.add_argument("--instance", help="BlueStacks internal or display instance name")
    parser.add_argument("--wait-seconds", type=int, default=30)
    args = parser.parse_args()

    if args.action == "instances":
        print_json(list_instances())
        return

    instance = choose_instance(args.instance)

    if args.action == "start":
        start_instance(instance)
    elif args.action == "connect":
        wait_for_device(instance)
        print_json(diagnostics(instance))
    elif args.action == "status":
        print_json(diagnostics(instance))
    elif args.action == "launch":
        wait_for_device(instance)
        launch_game(instance)
    elif args.action == "verify":
        wait_for_device(instance)
        verify_game(instance, args.wait_seconds)
    elif args.action == "capture":
        wait_for_device(instance)
        capture(instance)
    elif args.action == "all":
        start_instance(instance)
        wait_for_device(instance)
        info = diagnostics(instance)
        print_json(info)
        if not info["package"]["installed"]:
            raise RuntimeError(
                "BlueStacks is ready, but the game is not installed. Install "
                "com.got.globalru from Google Play, then run 'verify'."
            )
        verify_game(instance, args.wait_seconds)
        capture(instance)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"POC ERROR: {exc}", file=os.sys.stderr)
        raise SystemExit(1)
