"""Native ARM64 Android PoC for WAR BOT.

Goal: boot an ARM64 Android guest on an x86-64 Windows host without an
ARM->x86 Android native bridge. This is intentionally software-emulated
(QEMU TCG), so it trades speed for instruction-set compatibility.

The module does not patch the game, spoof emulator detection, bypass Play
Integrity, or alter APK native libraries.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path


SDK_ROOT = Path(os.environ.get("WAR_BOT_ANDROID_SDK", r"C:\Android\Sdk"))
RUNTIME_ROOT = Path(os.environ.get("WAR_BOT_ARM64_RUNTIME", r"C:\warbot_arm64_runtime"))
APKS_DIR = Path(os.environ.get("WAR_BOT_APKS_DIR", r"C:\warbot_emulator_poc\apks_1.12.10"))

PACKAGE = "com.got.globalru"
ACTIVITY = "com.unity3d.player.MyMainPlayerActivity"

DEFAULT_SYSTEM_IMAGE = os.environ.get(
    "WAR_BOT_ARM64_SYSTEM_IMAGE",
    "system-images;android-30;google_apis;arm64-v8a",
)
CONSOLE_PORT = int(os.environ.get("WAR_BOT_ARM64_CONSOLE_PORT", "5560"))
ADB_PORT = CONSOLE_PORT + 1
SERIAL = os.environ.get("WAR_BOT_ARM64_SERIAL", f"emulator-{CONSOLE_PORT}")
RAM_MB = int(os.environ.get("WAR_BOT_ARM64_RAM_MB", "4096"))
CPU_CORES = int(os.environ.get("WAR_BOT_ARM64_CPU_CORES", "4"))
CPU_MODEL = os.environ.get("WAR_BOT_ARM64_CPU", "cortex-a57")

ADB = SDK_ROOT / "platform-tools" / "adb.exe"
SDKMANAGER = SDK_ROOT / "cmdline-tools" / "latest" / "bin" / "sdkmanager.bat"
QEMU_DIR = SDK_ROOT / "emulator" / "qemu" / "windows-x86_64"
QEMU_ARM64 = QEMU_DIR / "qemu-system-aarch64.exe"
QEMU_ARM64_HEADLESS = QEMU_DIR / "qemu-system-aarch64-headless.exe"


def qemu_library_dirs() -> list[Path]:
    emulator_root = SDK_ROOT / "emulator"
    candidates = [
        QEMU_DIR,
        emulator_root,
        emulator_root / "lib64",
        emulator_root / "lib64" / "gles_swiftshader",
        emulator_root / "lib64" / "gles_angle",
        emulator_root / "lib64" / "gles_angle9",
        emulator_root / "lib64" / "gles_angle11",
        emulator_root / "lib64" / "qt" / "lib",
    ]
    return [path for path in candidates if path.is_dir()]


def qemu_environment() -> dict[str, str]:
    env = os.environ.copy()
    prefix = os.pathsep.join(str(path) for path in qemu_library_dirs())
    if prefix:
        env["PATH"] = prefix + os.pathsep + env.get("PATH", "")
    return env


def run(args, *, timeout=120, check=True, text=True, capture=True, env=None):
    result = subprocess.run(
        [str(x) for x in args],
        capture_output=capture,
        text=text,
        encoding="utf-8" if text else None,
        errors="replace" if text else None,
        timeout=timeout,
        check=False,
        env=env,
    )
    if check and result.returncode:
        detail = ""
        if text:
            detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(
            f"Command failed ({result.returncode}): {detail or 'no diagnostic output'}"
        )
    return result


def adb(*args, timeout=60, check=True, text=True):
    cmd = [ADB, "-s", SERIAL, *args]
    return run(cmd, timeout=timeout, check=check, text=text)


def package_dir(package_id: str = DEFAULT_SYSTEM_IMAGE) -> Path:
    parts = package_id.split(";")
    if len(parts) != 4 or parts[0] != "system-images":
        raise ValueError(f"Unexpected system image package id: {package_id}")
    return SDK_ROOT / "system-images" / parts[1] / parts[2] / parts[3]


def image_inventory(image_dir: Path) -> dict[str, str]:
    candidates = {
        "kernel": ("kernel-ranchu-64", "kernel-ranchu"),
        "ramdisk": ("ramdisk.img",),
        "system": ("system.img",),
        "vendor": ("vendor.img",),
        "userdata": ("userdata.img",),
        "encryptionkey": ("encryptionkey.img",),
    }
    result: dict[str, str] = {}
    for key, names in candidates.items():
        for name in names:
            path = image_dir / name
            if path.is_file():
                result[key] = str(path)
                break
    return result


def qemu_machine_probe() -> dict[str, object]:
    if not QEMU_ARM64.is_file():
        return {"returncode": None, "output": "", "names": []}
    result = run(
        [QEMU_ARM64, "-machine", "help"],
        timeout=30,
        check=False,
        env=qemu_environment(),
    )
    # QEMU builds on Windows are inconsistent about whether help goes to
    # stdout or stderr. Parse both so an empty stdout is not mistaken for
    # "no ARM64 machines".
    output = "\n".join(part for part in (result.stdout, result.stderr) if part)
    names: list[str] = []
    for raw in output.splitlines():
        line = raw.strip()
        if not line or line.lower().startswith("supported machines"):
            continue
        first = line.split()[0]
        if first and first[0].isalnum() and first not in names:
            names.append(first)
    return {
        "returncode": result.returncode,
        "output": output.strip(),
        "names": names,
    }


def qemu_machine_names() -> list[str]:
    return list(qemu_machine_probe()["names"])


def choose_machine(names: list[str] | None = None) -> str:
    names = names if names is not None else qemu_machine_names()
    if "ranchu" in names:
        return "ranchu"
    if "virt" in names:
        return "virt"
    raise RuntimeError(
        "qemu-system-aarch64 exposes neither 'ranchu' nor 'virt' machine"
    )


def probe() -> dict[str, object]:
    image_dir = package_dir()
    inventory = image_inventory(image_dir)
    machine_probe = qemu_machine_probe()
    machines = list(machine_probe["names"])
    return {
        "host": {
            "system": platform.system(),
            "machine": platform.machine(),
        },
        "goal": "full ARM64 guest; no Houdini/libndk_translation/native bridge",
        "sdk_root": str(SDK_ROOT),
        "tools": {
            "adb": ADB.is_file(),
            "sdkmanager": SDKMANAGER.is_file(),
            "qemu_system_aarch64": QEMU_ARM64.is_file(),
            "qemu_system_aarch64_headless": QEMU_ARM64_HEADLESS.is_file(),
        },
        "system_image": {
            "package": DEFAULT_SYSTEM_IMAGE,
            "path": str(image_dir),
            "installed": image_dir.is_dir(),
            "files": inventory,
        },
        "qemu": {
            "machines": machines,
            "selected_machine": choose_machine(machines) if machines else "",
            "machine_help_returncode": machine_probe["returncode"],
            "machine_help_returncode_hex": (
                f"0x{machine_probe['returncode'] & 0xFFFFFFFF:08X}"
                if isinstance(machine_probe["returncode"], int)
                else ""
            ),
            "machine_help_output": machine_probe["output"],
            "library_search_dirs": [str(path) for path in qemu_library_dirs()],
            "tcg": True,
            "hardware_acceleration": False,
        },
        "ports": {
            "console": CONSOLE_PORT,
            "adb": ADB_PORT,
            "serial": SERIAL,
        },
    }


def validate_tools(require_image=False):
    missing = [str(p) for p in (ADB, SDKMANAGER, QEMU_ARM64) if not p.is_file()]
    if missing:
        raise RuntimeError("Missing Android SDK tools:\n" + "\n".join(missing))
    if require_image:
        inv = image_inventory(package_dir())
        required = ("kernel", "ramdisk", "system", "userdata")
        absent = [name for name in required if name not in inv]
        if absent:
            raise RuntimeError(
                "ARM64 system image is incomplete; missing: " + ", ".join(absent)
            )


def install_system_image():
    validate_tools(require_image=False)
    if package_dir().is_dir() and image_inventory(package_dir()).get("system"):
        print(f"System image already installed: {DEFAULT_SYSTEM_IMAGE}")
        return
    result = run(
        [SDKMANAGER, DEFAULT_SYSTEM_IMAGE],
        timeout=1800,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(
            "sdkmanager could not install ARM64 image.\n"
            + (result.stderr or result.stdout or "").strip()
        )
    validate_tools(require_image=True)
    print(f"Installed: {DEFAULT_SYSTEM_IMAGE}")


def runtime_paths() -> dict[str, Path]:
    return {
        "userdata": RUNTIME_ROOT / "userdata-qemu.img",
        "hw": RUNTIME_ROOT / "hardware-qemu.ini",
        "stdout": RUNTIME_ROOT / "qemu-arm64.log",
        "pid": RUNTIME_ROOT / "qemu-arm64.pid",
        "crash": RUNTIME_ROOT / "game-crash.txt",
        "frame": RUNTIME_ROOT / "frame.png",
    }


def write_hw_ini(path: Path):
    content = f"""hw.cpu.arch = arm64
hw.cpu.ncore = {CPU_CORES}
hw.ramSize = {RAM_MB}
hw.screen = multi-touch
hw.mainKeys = no
hw.keyboard = yes
hw.lcd.width = 1060
hw.lcd.height = 2376
hw.lcd.depth = 16
hw.lcd.density = 480
hw.gpu.enabled = no
hw.audioInput = no
hw.audioOutput = no
hw.sdCard = no
disk.cachePartition = no
disk.dataPartition.size = 8589934592
vm.heapSize = 512
"""
    path.write_text(content, encoding="utf-8")


def prepare_runtime(wipe=False):
    validate_tools(require_image=True)
    RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
    inv = image_inventory(package_dir())
    paths = runtime_paths()
    if wipe or not paths["userdata"].is_file():
        shutil.copy2(inv["userdata"], paths["userdata"])
    write_hw_ini(paths["hw"])
    return inv, paths


def build_direct_qemu_command(*, window=False, wipe=False) -> list[str]:
    inv, paths = prepare_runtime(wipe=wipe)
    machine = choose_machine()
    cmd = [
        str(QEMU_ARM64),
        "-cpu", CPU_MODEL,
        "-machine", f"type={machine}",
        "-accel", "tcg,thread=multi",
        "-smp", str(CPU_CORES),
        "-m", str(RAM_MB),
        "-append",
        (
            "console=ttyAMA0,38400 keep_bootcon earlyprintk=ttyAMA0 "
            "androidboot.hardware=ranchu androidboot.serialno=WARBOTARM64"
        ),
        "-kernel", inv["kernel"],
        "-initrd", inv["ramdisk"],
        "-drive", f"index=0,id=system,file={inv['system']},format=raw,readonly=on",
        "-device", "virtio-blk-device,drive=system",
        "-drive", f"index=2,id=userdata,file={paths['userdata']},format=raw",
        "-device", "virtio-blk-device,drive=userdata",
    ]
    if "vendor" in inv:
        cmd += [
            "-drive", f"index=3,id=vendor,file={inv['vendor']},format=raw,readonly=on",
            "-device", "virtio-blk-device,drive=vendor",
        ]
    cmd += [
        "-netdev", "user,id=mynet",
        "-device", "virtio-net-device,netdev=mynet",
        "-android-ports", f"{CONSOLE_PORT},{ADB_PORT}",
        "-android-hw", str(paths["hw"]),
        "-L", str(SDK_ROOT / "emulator" / "pc-bios"),
    ]
    if not window:
        cmd += ["-display", "none"]
    return cmd


def start_direct(*, window=False, wipe=False, wait=True):
    _, paths = prepare_runtime(wipe=wipe)
    cmd = build_direct_qemu_command(window=window, wipe=False)
    log = open(paths["stdout"], "w", encoding="utf-8", errors="replace")
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    proc = subprocess.Popen(
        cmd,
        stdout=log,
        stderr=subprocess.STDOUT,
        creationflags=flags,
        env=qemu_environment(),
    )
    paths["pid"].write_text(str(proc.pid), encoding="ascii")
    print(f"Native ARM64 QEMU started: PID {proc.pid}")
    print(f"Log: {paths['stdout']}")
    if wait:
        wait_for_boot()
    return proc.pid


def wait_for_boot(timeout=1200):
    deadline = time.monotonic() + timeout
    last_state = ""
    while time.monotonic() < deadline:
        state = run([ADB, "-s", SERIAL, "get-state"], timeout=10, check=False)
        last_state = state.stdout.strip()
        if last_state == "device":
            boot = adb(
                "shell", "getprop", "sys.boot_completed", check=False
            ).stdout.strip()
            if boot == "1":
                print(f"ARM64 Android booted: {SERIAL}")
                return
        time.sleep(3)
    log_path = runtime_paths()["stdout"]
    tail = ""
    if log_path.is_file():
        tail = "\n".join(
            log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-80:]
        )
    raise TimeoutError(
        f"ARM64 guest did not boot; adb_state={last_state!r}\n"
        f"QEMU log tail:\n{tail}"
    )


def guest_status() -> dict[str, object]:
    state = run([ADB, "-s", SERIAL, "get-state"], check=False).stdout.strip()
    if state != "device":
        return {"serial": SERIAL, "device_state": state or "missing"}
    props = {
        "serial": SERIAL,
        "device_state": state,
        "boot_completed": adb("shell", "getprop", "sys.boot_completed", check=False).stdout.strip(),
        "android": adb("shell", "getprop", "ro.build.version.release", check=False).stdout.strip(),
        "abi": adb("shell", "getprop", "ro.product.cpu.abi", check=False).stdout.strip(),
        "abilist": adb("shell", "getprop", "ro.product.cpu.abilist", check=False).stdout.strip(),
        "native_bridge": adb("shell", "getprop", "ro.dalvik.vm.native.bridge", check=False).stdout.strip(),
        "model": adb("shell", "getprop", "ro.product.model", check=False).stdout.strip(),
        "resolution": adb("shell", "wm", "size", check=False).stdout.strip(),
        "density": adb("shell", "wm", "density", check=False).stdout.strip(),
    }
    props["native_arm64"] = is_native_arm64(props)
    return props


def is_native_arm64(props: dict[str, object]) -> bool:
    abi = str(props.get("abi", "")).lower()
    abilist = str(props.get("abilist", "")).lower()
    bridge = str(props.get("native_bridge", "")).strip().lower()
    return (
        abi == "arm64-v8a"
        and "x86" not in abilist
        and bridge in ("", "0", "none")
    )


def verify_native_arm64():
    props = guest_status()
    if not props.get("native_arm64"):
        raise RuntimeError(
            "NATIVE ARM64 GATE FAIL: guest still uses x86/native translation:\n"
            + json.dumps(props, ensure_ascii=False, indent=2)
        )
    print("NATIVE ARM64 GATE PASS: no x86 ABI and no Android native bridge")
    print(json.dumps(props, ensure_ascii=False, indent=2))


def validate_game_files():
    files = [
        APKS_DIR / "base.apk",
        APKS_DIR / "split_config.arm64_v8a.apk",
        APKS_DIR / "split_game_asset.apk",
    ]
    missing = [str(x) for x in files if not x.is_file()]
    if missing:
        raise RuntimeError("Missing game APKS:\n" + "\n".join(missing))
    return files


def install_game():
    verify_native_arm64()
    files = validate_game_files()
    result = adb(
        "install-multiple", "-r", *map(str, files),
        timeout=1200,
    )
    print(result.stdout.strip())


def launch_game():
    result = adb(
        "shell", "am", "start", "-W", "-n", f"{PACKAGE}/{ACTIVITY}",
        timeout=120,
    )
    print(result.stdout.strip())


def verify_game(wait_seconds=45):
    verify_native_arm64()
    adb("logcat", "-c", check=False)
    launch_game()
    time.sleep(wait_seconds)
    pid = adb("shell", "pidof", PACKAGE, check=False).stdout.strip()
    if pid:
        print(f"GAME PASS: {PACKAGE} alive as PID {pid}")
        return True
    crash = adb(
        "logcat", "-b", "crash", "-d", "-v", "threadtime",
        check=False, timeout=120,
    ).stdout
    path = runtime_paths()["crash"]
    path.write_text(crash, encoding="utf-8")
    raise RuntimeError(f"GAME FAIL: process exited; crash log={path}")


def capture() -> Path:
    path = runtime_paths()["frame"]
    result = subprocess.run(
        [str(ADB), "-s", SERIAL, "exec-out", "screencap", "-p"],
        capture_output=True,
        timeout=60,
        check=False,
    )
    if result.returncode or not result.stdout.startswith(b"\x89PNG"):
        raise RuntimeError("ADB screencap did not return a PNG")
    path.write_bytes(result.stdout)
    print(path)
    return path


def stop():
    result = adb("emu", "kill", check=False)
    if result.returncode == 0:
        print((result.stdout or result.stderr).strip())
        return
    pid_file = runtime_paths()["pid"]
    if pid_file.is_file() and os.name == "nt":
        pid = pid_file.read_text(encoding="ascii").strip()
        run(["taskkill", "/PID", pid, "/T", "/F"], check=False)
        print(f"Stopped QEMU PID {pid}")


def print_json(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description="WAR BOT native ARM64 Android PoC")
    parser.add_argument(
        "action",
        choices=(
            "probe", "install-image", "prepare", "command", "start",
            "status", "verify-native", "install-game", "launch",
            "verify-game", "capture", "stop", "all",
        ),
    )
    parser.add_argument("--window", action="store_true")
    parser.add_argument("--wipe", action="store_true")
    parser.add_argument("--wait-seconds", type=int, default=45)
    args = parser.parse_args()

    if args.action == "probe":
        print_json(probe())
    elif args.action == "install-image":
        install_system_image()
    elif args.action == "prepare":
        prepare_runtime(wipe=args.wipe)
        print(f"Runtime prepared: {RUNTIME_ROOT}")
    elif args.action == "command":
        print_json(build_direct_qemu_command(window=args.window, wipe=args.wipe))
    elif args.action == "start":
        start_direct(window=args.window, wipe=args.wipe)
    elif args.action == "status":
        print_json(guest_status())
    elif args.action == "verify-native":
        verify_native_arm64()
    elif args.action == "install-game":
        install_game()
    elif args.action == "launch":
        launch_game()
    elif args.action == "verify-game":
        verify_game(args.wait_seconds)
    elif args.action == "capture":
        capture()
    elif args.action == "stop":
        stop()
    elif args.action == "all":
        install_system_image()
        start_direct(window=args.window, wipe=args.wipe)
        verify_native_arm64()
        install_game()
        verify_game(args.wait_seconds)
        capture()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ARM64 POC ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
