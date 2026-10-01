"""Isolated Android Emulator compatibility probe for WAR BOT.

This module intentionally does not import or run the tutorial bot.  It proves
the emulator lifecycle and APK compatibility before DeviceBackend refactoring.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


SDK_ROOT = Path(os.environ.get("WAR_BOT_ANDROID_SDK", r"C:\Android\Sdk"))
JAVA_HOME = Path(os.environ.get(
    "WAR_BOT_JAVA_HOME", r"C:\Program Files\Microsoft\jdk-17.0.20.101-hotspot"
))
AVD_HOME = Path(os.environ.get("WAR_BOT_AVD_HOME", r"C:\Android\avd-home"))
APKS_DIR = Path(os.environ.get("WAR_BOT_APKS_DIR", r"C:\warbot_emulator_poc\apks_1.12.10"))
AVD_NAME = os.environ.get("WAR_BOT_AVD_NAME", "warbot_api34_x86_64")
SYSTEM_IMAGE = "system-images;android-34;google_apis_playstore;x86_64"
PACKAGE = "com.got.globalru"
ACTIVITY = "com.unity3d.player.MyMainPlayerActivity"
SERIAL = os.environ.get("WAR_BOT_EMULATOR_SERIAL", "emulator-5554")


def executable(relative):
    suffix = ".exe" if os.name == "nt" and not str(relative).endswith(".bat") else ""
    return SDK_ROOT / (str(relative) + suffix)


ADB = executable("platform-tools/adb")
EMULATOR = executable("emulator/emulator")
AVDMANAGER = SDK_ROOT / "cmdline-tools" / "latest" / "bin" / "avdmanager.bat"


def environment():
    env = os.environ.copy()
    env["ANDROID_SDK_ROOT"] = str(SDK_ROOT)
    env["ANDROID_AVD_HOME"] = str(AVD_HOME)
    env["ANDROID_SDK_HOME"] = str(AVD_HOME.parent)
    env["JAVA_HOME"] = str(JAVA_HOME)
    env["PATH"] = str(JAVA_HOME / "bin") + os.pathsep + env.get("PATH", "")
    return env


def run(args, *, input_text=None, timeout=120, check=True, capture=True):
    result = subprocess.run(
        [str(value) for value in args], input=input_text, text=True,
        encoding="utf-8", errors="replace", capture_output=capture,
        timeout=timeout, check=False, env=environment(),
    )
    if check and result.returncode:
        detail = (result.stderr or result.stdout or "unknown error").strip()
        raise RuntimeError(f"Command failed ({result.returncode}): {detail}")
    return result


def adb(*args, timeout=60, check=True):
    return run([ADB, "-s", SERIAL, *args], timeout=timeout, check=check)


def validate_files():
    required = [ADB, EMULATOR, AVDMANAGER]
    required += [
        APKS_DIR / "base.apk",
        APKS_DIR / "split_config.arm64_v8a.apk",
        APKS_DIR / "split_game_asset.apk",
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise RuntimeError("Missing PoC files:\n" + "\n".join(missing))


def avd_exists():
    result = run([EMULATOR, "-list-avds"], check=False)
    return AVD_NAME in result.stdout.splitlines()


def create_avd():
    validate_files()
    AVD_HOME.mkdir(parents=True, exist_ok=True)
    if avd_exists():
        print(f"AVD already exists: {AVD_NAME}")
        return
    result = run(
        [AVDMANAGER, "create", "avd", "--name", AVD_NAME,
         "--package", SYSTEM_IMAGE, "--device", "pixel_7"],
        input_text="no\n", timeout=180,
    )
    print(result.stdout.strip())
    if not avd_exists():
        raise RuntimeError("avdmanager returned without creating the requested AVD")
    print(f"AVD created: {AVD_NAME}")


def start_emulator(window=False, wipe=False):
    create_avd()
    args = [
        EMULATOR, f"@{AVD_NAME}", "-port", "5554", "-no-audio",
        "-no-boot-anim", "-gpu", "swiftshader_indirect",
        "-skin", "1060x2376", "-dpi-device", "480",
    ]
    if not window:
        args.append("-no-window")
    if wipe:
        args.append("-wipe-data")
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    process = subprocess.Popen(
        [str(value) for value in args], env=environment(),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=flags,
    )
    print(f"Emulator process started: PID {process.pid}")
    wait_for_boot()
    return process.pid


def wait_for_boot(timeout=600):
    deadline = time.monotonic() + timeout
    run([ADB, "wait-for-device"], timeout=timeout)
    last = ""
    while time.monotonic() < deadline:
        result = adb("shell", "getprop", "sys.boot_completed", check=False)
        last = result.stdout.strip()
        if last == "1":
            adb("shell", "wm", "size", "1060x2376")
            adb("shell", "wm", "density", "480")
            adb("shell", "settings", "put", "global", "window_animation_scale", "0")
            adb("shell", "settings", "put", "global", "transition_animation_scale", "0")
            adb("shell", "settings", "put", "global", "animator_duration_scale", "0")
            print("Android boot completed")
            return
        time.sleep(2)
    raise TimeoutError(f"Android boot timed out; sys.boot_completed={last!r}")


def install_apks():
    validate_files()
    apks = [
        APKS_DIR / "base.apk",
        APKS_DIR / "split_config.arm64_v8a.apk",
        APKS_DIR / "split_game_asset.apk",
    ]
    result = adb("install-multiple", "-r", *map(str, apks), timeout=900)
    print(result.stdout.strip())


def launch_game():
    result = adb("shell", "am", "start", "-W", "-n", f"{PACKAGE}/{ACTIVITY}", timeout=120)
    print(result.stdout.strip())


def verify_game(wait_seconds=15):
    launch_game()
    time.sleep(wait_seconds)
    pid = adb("shell", "pidof", PACKAGE, check=False).stdout.strip()
    if pid:
        print(f"G2 PASS: {PACKAGE} is still running as PID {pid}")
        return True
    report = adb("shell", "dumpsys", "dropbox", "--print", "data_app_crash", check=False).stdout
    evidence_dir = APKS_DIR.parent
    evidence_dir.mkdir(parents=True, exist_ok=True)
    evidence = evidence_dir / "g2_data_app_crash.txt"
    evidence.write_text(report, encoding="utf-8")
    raise RuntimeError(
        f"G2 FAIL: {PACKAGE} exited after launch. Crash evidence: {evidence}"
    )


def stop_emulator():
    result = adb("emu", "kill", check=False)
    print((result.stdout or result.stderr).strip())


def status():
    props = {
        "serial": SERIAL,
        "avd": AVD_NAME,
        "device_state": run([ADB, "-s", SERIAL, "get-state"], check=False).stdout.strip(),
        "boot_completed": adb("shell", "getprop", "sys.boot_completed", check=False).stdout.strip(),
        "android": adb("shell", "getprop", "ro.build.version.release", check=False).stdout.strip(),
        "abi": adb("shell", "getprop", "ro.product.cpu.abilist", check=False).stdout.strip(),
        "resolution": adb("shell", "wm", "size", check=False).stdout.strip(),
        "density": adb("shell", "wm", "density", check=False).stdout.strip(),
        "package": adb("shell", "dumpsys", "package", PACKAGE, check=False).stdout.find("versionName=") >= 0,
        "pid": adb("shell", "pidof", PACKAGE, check=False).stdout.strip(),
    }
    print(json.dumps(props, ensure_ascii=False, indent=2))
    return props


def main():
    parser = argparse.ArgumentParser(description="WAR BOT Android Emulator PoC")
    parser.add_argument("action", choices=("create", "start", "install", "launch", "verify", "status", "stop", "all"))
    parser.add_argument("--window", action="store_true", help="Show the emulator window")
    parser.add_argument("--wipe", action="store_true", help="Start from clean AVD data")
    args = parser.parse_args()

    if args.action == "create":
        create_avd()
    elif args.action == "start":
        start_emulator(args.window, args.wipe)
    elif args.action == "install":
        install_apks()
    elif args.action == "launch":
        launch_game()
    elif args.action == "verify":
        verify_game()
    elif args.action == "status":
        status()
    elif args.action == "stop":
        stop_emulator()
    elif args.action == "all":
        start_emulator(args.window, args.wipe)
        install_apks()
        verify_game()
        status()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"POC ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
