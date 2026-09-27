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
import re
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
SERIAL = os.environ.get("WAR_BOT_ARM64_SERIAL", f"127.0.0.1:{ADB_PORT}")
RAM_MB = int(os.environ.get("WAR_BOT_ARM64_RAM_MB", "4096"))
CPU_CORES = int(os.environ.get("WAR_BOT_ARM64_CPU_CORES", "4"))
CPU_MODEL = os.environ.get("WAR_BOT_ARM64_CPU", "cortex-a57")
POST_ADB_STALL_SECONDS = int(
    os.environ.get("WAR_BOT_ARM64_POST_ADB_TIMEOUT", "180")
)
GPU_MODE = os.environ.get("WAR_BOT_ARM64_GPU", "host")
# Prefer Google's Android-modified ARM virt board. AOSP extended this board
# with ranchu/goldfish devices while retaining PSCI under TCG, unlike the
# legacy ranchu board which exposes no PSCI for TCG and therefore boots CPU0 only.
GOOGLE_ARM64_MACHINE = os.environ.get(
    "WAR_BOT_ARM64_MACHINE", "virt"
).strip().lower()
SERIAL_CONSOLE = os.environ.get("WAR_BOT_ARM64_SERIAL_CONSOLE", "")
RANCHU_BOOT_DEVICE = os.environ.get(
    "WAR_BOT_ARM64_BOOT_DEVICE", "a003600.virtio_mmio"
)

ADB = SDK_ROOT / "platform-tools" / "adb.exe"
SDKMANAGER = SDK_ROOT / "cmdline-tools" / "latest" / "bin" / "sdkmanager.bat"
QEMU_DIR = SDK_ROOT / "emulator" / "qemu" / "windows-x86_64"
QEMU_ARM64 = QEMU_DIR / "qemu-system-aarch64.exe"
QEMU_ARM64_HEADLESS = QEMU_DIR / "qemu-system-aarch64-headless.exe"
UPSTREAM_QEMU_ARM64 = Path(os.environ.get(
    "WAR_BOT_UPSTREAM_QEMU",
    r"C:\Program Files\qemu\qemu-system-aarch64.exe",
))
UPSTREAM_QEMU_IMG = UPSTREAM_QEMU_ARM64.with_name("qemu-img.exe")
SDK_QEMU_IMG = SDK_ROOT / "emulator" / "qemu-img.exe"
QEMU_IMG = SDK_QEMU_IMG if SDK_QEMU_IMG.is_file() else UPSTREAM_QEMU_IMG
MKE2FS = SDK_ROOT / "platform-tools" / "mke2fs.exe"
DATA_SIZE_BYTES = int(os.environ.get("WAR_BOT_ARM64_DATA_BYTES", str(8 * 1024**3)))


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
    qemu = UPSTREAM_QEMU_ARM64 if UPSTREAM_QEMU_ARM64.is_file() else QEMU_ARM64
    if not qemu.is_file():
        return {"returncode": None, "output": "", "names": []}
    args = [qemu, "-machine", "help"]
    if qemu == QEMU_ARM64:
        args.insert(1, "-qemu")
    result = run(
        args,
        timeout=30,
        check=False,
        env=qemu_environment(),
    )
    # QEMU builds on Windows are inconsistent about whether help goes to
    # stdout or stderr. Parse both so an empty stdout is not mistaken for
    # "no ARM64 machines".
    output = "\n".join(part for part in (result.stdout, result.stderr) if part)
    names: list[str] = []
    if result.returncode == 0:
        in_machine_list = False
        for raw in output.splitlines():
            line = raw.strip()
            if not in_machine_list:
                if line.lower().startswith("supported machines"):
                    in_machine_list = True
                continue
            if not line:
                continue
            if line.startswith(("INFO", "WARNING")) or line.startswith(str(qemu)):
                break
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


def choose_machine(names: list[str] | None = None, *, strict=True) -> str:
    names = names if names is not None else qemu_machine_names()
    if "ranchu" in names:
        return "ranchu"
    if "virt" in names:
        return "virt"

    # Some QEMU builds expose only versioned ARM virtual-machine names
    # (for example virt-8.2) instead of the unversioned "virt" alias.
    versioned_virt = [name for name in names if name.startswith("virt-")]
    if versioned_virt:
        return versioned_virt[0]

    if strict:
        raise RuntimeError(
            "qemu-system-aarch64 exposes no ranchu/virt-compatible machine; "
            f"available={names}"
        )
    return ""


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
            "upstream_qemu_system_aarch64": UPSTREAM_QEMU_ARM64.is_file(),
        },
        "system_image": {
            "package": DEFAULT_SYSTEM_IMAGE,
            "path": str(image_dir),
            "installed": image_dir.is_dir(),
            "files": inventory,
        },
        "graphics": {
            "mode": GPU_MODE,
            "vulkan_guest": False,
        },
        "qemu": {
            "machines": machines,
            "selected_machine": choose_machine(machines, strict=False),
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


def validate_tools(require_image=False, require_upstream=False):
    required = [
        ADB, SDKMANAGER, QEMU_ARM64, QEMU_ARM64_HEADLESS, QEMU_IMG, MKE2FS,
    ]
    if require_upstream:
        required.append(UPSTREAM_QEMU_ARM64)
    missing = [str(p) for p in required if not p.is_file()]
    if missing:
        raise RuntimeError("Missing Android/QEMU tools:\n" + "\n".join(missing))
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
        "cache": RUNTIME_ROOT / "cache-qemu.qcow2",
        "encryptionkey": RUNTIME_ROOT / "encryptionkey-qemu.qcow2",
        "hw": RUNTIME_ROOT / "hardware-qemu.ini",
        "stdout": RUNTIME_ROOT / "qemu-arm64.log",
        "pid": RUNTIME_ROOT / "qemu-arm64.pid",
        "machine_stamp": RUNTIME_ROOT / "machine.txt",
        "crash": RUNTIME_ROOT / "game-crash.txt",
        "boot_crash": RUNTIME_ROOT / "zygote-crash.txt",
        "adb_crash": RUNTIME_ROOT / "adb-crash-buffer.txt",
        "adb_logcat_all": RUNTIME_ROOT / "adb-logcat-all.txt",
        "tombstone_probe": RUNTIME_ROOT / "tombstone-probe.txt",
        "boot_live_state": RUNTIME_ROOT / "boot-live-state.txt",
        "tombstones": RUNTIME_ROOT / "tombstones",
        "boot_report": RUNTIME_ROOT / "boot-diagnostic.json",
        "frame": RUNTIME_ROOT / "frame.png",
        "pstore": RUNTIME_ROOT / "pstore.bin",
        "base_dtb": RUNTIME_ROOT / "ranchu-base.dtb",
        "dtb": RUNTIME_ROOT / "ranchu-warbot.dtb",
    }


def write_hw_ini(path: Path, inv: dict[str, str], paths: dict[str, Path]):
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
hw.gpu.enabled = yes
hw.gpu.mode = {GPU_MODE}
hw.audioInput = no
hw.audioOutput = no
hw.sdCard = no
disk.cachePartition = no
disk.cachePartition.path = {paths['cache']}
kernel.path = {inv['kernel']}
disk.ramdisk.path = {inv['ramdisk']}
disk.systemPartition.initPath = {inv['system']}
disk.vendorPartition.initPath = {inv['vendor']}
disk.dataPartition.path = {paths['userdata']}
disk.dataPartition.initPath = {inv['userdata']}
disk.encryptionKeyPartition.path = {paths['encryptionkey']}
disk.dataPartition.size = 8589934592
vm.heapSize = 512
"""
    path.write_text(content, encoding="utf-8")


def prepare_runtime(wipe=False):
    validate_tools(require_image=True)
    RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
    inv = image_inventory(package_dir())
    paths = runtime_paths()
    paths.setdefault("pstore", RUNTIME_ROOT / "pstore.bin")
    if wipe or not paths["userdata"].is_file():
        if paths["userdata"].is_file():
            paths["userdata"].unlink()
        run([QEMU_IMG, "create", "-f", "raw", paths["userdata"], DATA_SIZE_BYTES])
        blocks = DATA_SIZE_BYTES // 4096
        run([
            MKE2FS, "-t", "ext4", "-F", "-b", "4096", "-L", "data",
            # Android 11 ships e2fsck 1.45.4. Host mke2fs 1.47 enables
            # orphan_file (FEATURE_C12) by default; the guest cannot validate
            # it and remounts /data read-only, causing init_user0_failed.
            "-O", "^orphan_file", "-m", "0", paths["userdata"], blocks,
        ], timeout=600)
    if wipe or not paths["cache"].is_file():
        if paths["cache"].is_file():
            paths["cache"].unlink()
        run([QEMU_IMG, "create", "-f", "qcow2", paths["cache"], "256M"])
    if wipe or not paths["encryptionkey"].is_file():
        if paths["encryptionkey"].is_file():
            paths["encryptionkey"].unlink()
        run([
            QEMU_IMG, "create", "-f", "qcow2", "-F", "raw",
            "-b", inv["encryptionkey"], paths["encryptionkey"],
        ])
    write_hw_ini(paths["hw"], inv, paths)
    if wipe or not paths["pstore"].is_file():
        paths["pstore"].write_bytes(b"\0" * 65536)
    return inv, paths


def build_direct_qemu_command(*, window=False, wipe=False) -> list[str]:
    inv, paths = prepare_runtime(wipe=wipe)
    image_dir = package_dir()
    machine = choose_machine()
    if machine != "virt":
        raise RuntimeError(f"Upstream ARM64 QEMU requires virt machine; selected={machine!r}")

    # The Google launcher currently exits before entering its QEMU main loop on
    # an x86-64 Windows host. Upstream QEMU can boot this ARM64 kernel on `virt`;
    # keep the launcher-observed block ordering (vendor, encryption, userdata,
    # cache, system), which Android's first-stage init relies on.
    append = (
        "console=ttyAMA0,38400 keep_bootcon earlycon=pl011,0x09000000 "
        "loop.max_part=7 printk.devkmsg=on "
        "androidboot.boot_devices=a003600.virtio_mmio "
        "androidboot.logical_partitions=1 "
        "androidboot.hardware=ranchu androidboot.serialno=WARBOTARM64 "
        "qemu=1 androidboot.qemu=1 qemu.encrypt=1 "
        "qemu.media.ccodec=0 "
        "qemu.gles=0 qemu.virtiowifi=0"
    )
    cmd = [
        str(UPSTREAM_QEMU_ARM64),
        "-machine", "virt",
        "-cpu", CPU_MODEL,
        "-accel", "tcg,thread=multi",
        "-smp", str(CPU_CORES),
        "-m", str(RAM_MB),
        "-kernel", inv["kernel"],
        "-initrd", inv["ramdisk"],
        "-append", append,
        "-nodefaults",
        "-no-reboot",
        "-serial", "stdio",
        "-monitor", "none",
        "-drive", f"if=none,id=vendor,file={inv['vendor']},format=raw,readonly=on",
        "-device", "virtio-blk-device,drive=vendor",
        "-drive", f"if=none,id=encrypt,file={paths['encryptionkey']},format=qcow2",
        "-device", "virtio-blk-device,drive=encrypt",
        "-drive", f"if=none,id=userdata,file={paths['userdata']},format=raw",
        "-device", "virtio-blk-device,drive=userdata",
        "-drive", f"if=none,id=cache,file={paths['cache']},format=qcow2",
        "-device", "virtio-blk-device,drive=cache",
        "-drive", f"if=none,id=system,file={inv['system']},format=raw,readonly=on",
        "-device", "virtio-blk-device,drive=system",
        "-netdev", f"user,id=mynet,hostfwd=tcp:127.0.0.1:{ADB_PORT}-:5555",
        "-device", "virtio-net-device,netdev=mynet",
        "-device", "virtio-rng-device",
    ]
    cmd += ["-display", "sdl" if window else "none"]
    return cmd


def google_arm64_machine() -> str:
    machine = GOOGLE_ARM64_MACHINE
    if machine not in {"virt", "ranchu"}:
        raise RuntimeError(
            f"WAR_BOT_ARM64_MACHINE must be 'virt' or 'ranchu', got {machine!r}"
        )
    return machine


def build_google_arm64_command(*, window=False, wipe=False) -> list[str]:
    """Run Google's ARM64 QEMU core directly without launcher-added HDA.

    The Android-modified virt board is the production default. AOSP added
    ranchu/goldfish pipe, framebuffer and audio devices to this board, while
    normal virt PSCI remains available under TCG. The legacy ranchu board
    stays available only as an opt-in comparison mode.
    """
    inv, paths = prepare_runtime(wipe=wipe)
    machine = google_arm64_machine()
    append = (
        "8250.nr_uarts=1 no_timer_check console=ttyAMA0,38400 keep_bootcon "
        "earlyprintk=ttyAMA0 loop.max_part=7 printk.devkmsg=on "
        f"android.qemud=1 androidboot.boot_devices={RANCHU_BOOT_DEVICE} "
        "androidboot.logical_partitions=1 "
        "androidboot.hardware=ranchu androidboot.serialno=WARBOTARM64 "
        "androidboot.vbmeta.digest=15e6b2e26d1523b6c38c0a60d5ac8f8cf547364c343d16e58338814e45faa6a8 "
        "androidboot.vbmeta.hash_alg=sha256 androidboot.vbmeta.size=6720 "
        "qemu=1 androidboot.qemu=1 qemu.encrypt=1 qemu.gles=1 "
        "qemu.media.ccodec=0 "
        "qemu.gltransport=pipe qemu.opengles.version=131072 "
        "qemu.skin=1060x2376 qemu.virtiowifi=0 qemu.vsync=60"
    )
    qemu = QEMU_ARM64 if window else QEMU_ARM64_HEADLESS
    cmd = [
        str(qemu), "-fuchsia", "-gpu", GPU_MODE,
        "-window-size", "1060x2376",
        "-L", str(SDK_ROOT / "emulator" / "lib" / "pc-bios"),
        "-machine", f"type={machine}", "-cpu", CPU_MODEL,
        "-smp", f"cores={CPU_CORES}", "-m", str(RAM_MB),
        "-lcd-density", "480", "-nodefaults", "-no-audio",
    ]

    if machine == "ranchu":
        cmd += [
            "-device",
            f"goldfish_pstore,addr=0xff018000,size=0x10000,file={paths['pstore']}",
        ]

    cmd += [
        "-kernel", inv["kernel"], "-initrd", inv["ramdisk"],
        "-drive", f"index=0,id=vendor,if=none,file={inv['vendor']},read-only",
        "-device", "virtio-blk-device,drive=vendor",
        "-drive", f"index=1,id=encrypt,if=none,file={paths['encryptionkey']}",
        "-device", "virtio-blk-device,drive=encrypt",
        "-drive", f"index=2,id=userdata,if=none,file={paths['userdata']},format=raw",
        "-device", "virtio-blk-device,drive=userdata",
        "-drive", f"index=3,id=cache,if=none,file={paths['cache']}",
        "-device", "virtio-blk-device,drive=cache",
        "-drive", f"index=4,id=system,if=none,file={inv['system']},read-only",
        "-device", "virtio-blk-device,drive=system",
        "-netdev", "user,id=mynet", "-device", "virtio-net-device,netdev=mynet",
        "-device", "virtio-rng-device", "-show-cursor",
        "-android-ports", f"{CONSOLE_PORT},{ADB_PORT}",
        "-serial", SERIAL_CONSOLE or ("con:" if window else "stdio"),
        "-append", append, "-android-hw", str(paths["hw"]),
    ]

    for prop in (
        "qemu.sf.lcd_density=480",
        "qemu.hw.mainkeys=0",
        "qemu.gles=1",
        "qemu.gltransport=pipe",
        "qemu.vsync=60",
        "qemu.media.ccodec=0",
        "qemu.camera_protocol_ver=1",
        "qemu.camera_hq_edge_processing=0",
    ):
        cmd += ["-boot-property", prop]

    return cmd


def build_google_ranchu_command(*, window=False, wipe=False) -> list[str]:
    """Compatibility alias for old local scripts."""
    return build_google_arm64_command(window=window, wipe=wipe)

def _set_fstab_node(tree, name, dev, mount_flags, fs_mgr_flags):
    node = f"/firmware/android/fstab/{name}"
    tree.set_property("compatible", f"android,{name}", node)
    tree.set_property("dev", dev, node)
    tree.set_property("type", "ext4", node)
    tree.set_property("mnt_flags", mount_flags, node)
    tree.set_property("fsmgr_flags", fs_mgr_flags, node)


def build_dynamic_partition_dtb(base_path: Path, output_path: Path):
    try:
        import fdt
    except ImportError as exc:
        raise RuntimeError("Python package 'fdt' is required; install requirements.txt") from exc

    tree = fdt.parse_dtb(base_path.read_bytes())
    logical_flags = "wait,logical,first_stage_mount"
    for name in ("system", "vendor", "product", "system_ext"):
        _set_fstab_node(tree, name, name, "ro,barrier=1", logical_flags)
    _set_fstab_node(
        tree, "metadata",
        "/dev/block/platform/a003c00.virtio_mmio/by-name/metadata",
        "noatime,nosuid,nodev", "wait,formattable,first_stage_mount",
    )
    tree.set_property("compatible", "android,vbmeta", "/firmware/android/vbmeta")
    tree.set_property(
        "parts", "vbmeta,system,vendor,product,system_ext",
        "/firmware/android/vbmeta",
    )
    tree.set_property(
        "by_name_prefix", "/dev/block/platform/a003600.virtio_mmio/by-name/",
        "/firmware/android/vbmeta",
    )
    output_path.write_bytes(tree.to_dtb())


def ensure_dynamic_partition_dtb(cmd: list[str], *, wipe=False) -> Path:
    dump_cmd = list(cmd)
    machine_arg_index = dump_cmd.index("-machine") + 1
    machine_arg = str(dump_cmd[machine_arg_index])
    machine = machine_arg.split(",", 1)[0].split("=", 1)[-1]
    base_dtb = RUNTIME_ROOT / f"{machine}-base.dtb"
    patched_dtb = RUNTIME_ROOT / f"{machine}-warbot.dtb"

    if wipe or not patched_dtb.is_file():
        base_dtb.unlink(missing_ok=True)
        dump_cmd[machine_arg_index] = (
            f"type={machine},dumpdtb={base_dtb.as_posix()}"
        )
        proc = subprocess.Popen(
            dump_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=qemu_environment(),
        )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if base_dtb.is_file() and base_dtb.stat().st_size:
                break
            if proc.poll() is not None:
                raise RuntimeError(
                    f"Google {machine} exited before producing its base DTB"
                )
            time.sleep(0.2)
        else:
            raise RuntimeError(f"Timed out while dumping Google {machine} DTB")
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        build_dynamic_partition_dtb(base_dtb, patched_dtb)
    return patched_dtb

def start_direct(*, window=False, wipe=False, wait=True):
    machine = google_arm64_machine()
    paths = runtime_paths()
    previous_machine = ""
    if paths["machine_stamp"].is_file():
        previous_machine = paths["machine_stamp"].read_text(
            encoding="ascii", errors="ignore"
        ).strip().lower()

    # A machine-topology switch changes DT, MMIO and boot assumptions. Reusing
    # an encrypted userdata/cache runtime across that switch makes diagnosis
    # ambiguous, so recreate only the disposable emulator runtime. PC-side
    # WAR BOT state/counters live outside RUNTIME_ROOT and are untouched.
    legacy_ranchu_runtime = (
        not previous_machine
        and machine == "virt"
        and (RUNTIME_ROOT / "ranchu-warbot.dtb").is_file()
    )
    if previous_machine and previous_machine != machine:
        print(
            f"ARM64 machine changed {previous_machine} -> {machine}; "
            "recreating emulator runtime.",
            flush=True,
        )
        wipe = True
    elif legacy_ranchu_runtime:
        print(
            "Legacy ranchu runtime detected; recreating disposable Android "
            "runtime for Google virt.",
            flush=True,
        )
        wipe = True

    _, paths = prepare_runtime(wipe=wipe)
    cmd = build_google_arm64_command(window=window, wipe=False)
    dtb = ensure_dynamic_partition_dtb(cmd, wipe=wipe)
    paths["machine_stamp"].write_text(machine, encoding="ascii")
    cmd += ["-dtb", str(dtb)]
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
        wait_for_boot(process=proc)
    return proc.pid


def _qemu_log_tail(lines=120):
    log_path = runtime_paths()["stdout"]
    if not log_path.is_file():
        return ""
    return "\n".join(
        log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
    )


def collect_boot_crash() -> dict[str, object]:
    """Extract the first useful Android boot crash from the serial/QEMU log.

    The native guest can fail before ADB is available, so serial output is the
    authoritative channel.  This collector deliberately does not modify the
    guest or skip any service; it only preserves evidence for diagnosis.
    """
    paths = runtime_paths()
    log_path = paths["stdout"]
    report: dict[str, object] = {
        "log": str(log_path),
        "crash_file": str(paths["boot_crash"]),
        "found": False,
        "process": "",
        "signal": "",
        "si_code": "",
        "fault_addr": "",
        "pc": "",
        "lr": "",
        "library": "",
        "build_id": "",
        "backtrace": [],
    }
    if not log_path.is_file():
        return report

    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    # Prefer crashes tied to zygote/app_process; otherwise retain a Codec2
    # crash because it is the current reproducible blocker.
    candidates = []
    for i, line in enumerate(lines):
        low = line.lower()
        if "signal 11" in low or "sigsegv" in low:
            window = "\n".join(lines[max(0, i - 25): min(len(lines), i + 180)]).lower()
            score = 0
            if "zygote" in window or "app_process64" in window:
                score += 4
            if "libcodec2_vndk.so" in window:
                score += 3
            if "backtrace:" in window:
                score += 2
            candidates.append((score, i))
    if not candidates:
        return report

    _, start_index = max(candidates, key=lambda item: item[0])
    start = max(0, start_index - 35)
    end = min(len(lines), start_index + 220)
    block_lines = lines[start:end]
    block = "\n".join(block_lines)

    # Trim at the beginning of a later unrelated fatal block if present.
    fatal_seen = 0
    trimmed = []
    for line in block_lines:
        if "fatal signal" in line.lower() or re.search(r"\bsignal\s+11\b", line.lower()):
            fatal_seen += 1
            if fatal_seen > 2 and len(trimmed) > 25:
                break
        trimmed.append(line)
    block = "\n".join(trimmed)

    def first(pattern: str, flags=re.IGNORECASE) -> str:
        match = re.search(pattern, block, flags)
        return match.group(1).strip() if match else ""

    report["found"] = True
    report["process"] = (
        first(r"(?:Cmdline:|>>>)[ \t]*(?:>>>[ \t]*)?([^\n<]+)")
        or ("zygote64" if "zygote64" in block.lower() else
            "zygote" if "zygote" in block.lower() else
            "app_process64" if "app_process64" in block.lower() else "")
    )
    report["signal"] = first(r"(signal\s+\d+\s*\([^\n]+?\))")
    report["si_code"] = first(r"(?:code|si_code)[=: ]+([^,\n]+)")
    report["fault_addr"] = first(r"(?:fault addr|si_addr)[=: ]+([^,\s\n]+)")
    report["pc"] = first(r"\bpc\s+([0-9a-fx]+)")
    report["lr"] = first(r"\blr\s+([0-9a-fx]+)")
    report["build_id"] = first(r"libcodec2_vndk\.so[^\n]*BuildId:\s*([0-9a-f]+)")
    report["library"] = "libcodec2_vndk.so" if "libcodec2_vndk.so" in block else ""

    frames = []
    for line in trimmed:
        if re.search(r"#\d+\s+pc\s+", line) or (
            "libcodec2_vndk.so" in line and " pc " in line.lower()
        ):
            frames.append(line.strip())
    report["backtrace"] = frames[:80]

    header = [
        "WAR BOT native ARM64 boot crash",
        f"process: {report['process']}",
        f"signal: {report['signal']}",
        f"si_code: {report['si_code']}",
        f"fault_addr: {report['fault_addr']}",
        f"pc: {report['pc']}",
        f"lr: {report['lr']}",
        f"library: {report['library']}",
        f"build_id: {report['build_id']}",
        "",
        "---- captured serial context ----",
        block,
        "",
    ]
    paths["boot_crash"].write_text("\n".join(header), encoding="utf-8", errors="replace")
    return report


def collect_adb_live_state() -> dict[str, object]:
    """Capture cheap pre-framework state without requiring system_server."""
    paths = runtime_paths()
    sections: list[str] = []
    result = {
        "path": "",
        "saved": False,
        "online_cpus": "",
    }

    commands = (
        ("getprop", ["shell", "getprop"]),
        ("cpuinfo", ["shell", "cat", "/proc/cpuinfo"]),
        ("online-cpus", ["shell", "cat", "/sys/devices/system/cpu/online"]),
        ("processes", ["shell", "ps", "-A"]),
    )
    for title, args in commands:
        try:
            proc = run(
                [ADB, "-s", SERIAL, *args],
                timeout=10,
                check=False,
            )
        except (subprocess.TimeoutExpired, OSError):
            sections.append(f"===== {title} =====\n<timeout/unavailable>\n")
            continue
        body = (proc.stdout or proc.stderr or "").strip()
        sections.append(f"===== {title} =====\n{body}\n")
        if title == "online-cpus":
            result["online_cpus"] = body

    if sections:
        paths["boot_live_state"].write_text(
            "\n".join(sections),
            encoding="utf-8",
            errors="replace",
        )
        result["path"] = str(paths["boot_live_state"])
        result["saved"] = True
    return result


def collect_adb_boot_diagnostics() -> dict[str, object]:
    """Best-effort Android crash evidence once adbd is reachable.

    Always materialize the diagnostic files. An empty crash log is itself
    useful evidence and must not look like a missing collector run.
    """
    paths = runtime_paths()
    result: dict[str, object] = {
        "crash_buffer": str(paths["adb_crash"]),
        "logcat_all": "",
        "tombstones_dir": "",
        "tombstone_probe": "",
        "crash_buffer_saved": False,
        "crash_buffer_empty": False,
        "logcat_all_saved": False,
        "tombstones_pulled": False,
    }

    try:
        state = run(
            [ADB, "-s", SERIAL, "get-state"],
            timeout=5, check=False,
        ).stdout.strip()
    except (subprocess.TimeoutExpired, OSError) as exc:
        paths["adb_crash"].write_text(
            f"<adb unavailable: {exc}>\n",
            encoding="utf-8",
            errors="replace",
        )
        return result

    if state != "device":
        paths["adb_crash"].write_text(
            f"<adb state is {state or 'missing'}; crash buffer not queried>\n",
            encoding="utf-8",
            errors="replace",
        )
        return result

    try:
        crash = run(
            [ADB, "-s", SERIAL, "logcat", "-b", "crash", "-d", "-v", "threadtime"],
            timeout=12, check=False,
        )
        text = (crash.stdout or "").strip()
        stderr = (crash.stderr or "").strip()
        if text:
            body = text + "\n"
            result["crash_buffer_saved"] = True
        else:
            body = (
                "<Android crash log buffer returned no records>\n"
                f"returncode={crash.returncode}\n"
                f"stderr={stderr or '<empty>'}\n"
            )
            result["crash_buffer_empty"] = True
        paths["adb_crash"].write_text(
            body,
            encoding="utf-8",
            errors="replace",
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        paths["adb_crash"].write_text(
            f"<crash-buffer query failed: {exc}>\n",
            encoding="utf-8",
            errors="replace",
        )

    # When -b crash is empty, logd's other buffers can still contain init,
    # linker, libc or service diagnostics useful for early userspace crashes.
    if result["crash_buffer_empty"]:
        try:
            all_logs = run(
                [ADB, "-s", SERIAL, "logcat", "-b", "all", "-d", "-v", "threadtime"],
                timeout=30, check=False,
            )
            all_text = all_logs.stdout or all_logs.stderr or ""
            if all_text.strip():
                paths["adb_logcat_all"].write_text(
                    all_text,
                    encoding="utf-8",
                    errors="replace",
                )
                result["logcat_all"] = str(paths["adb_logcat_all"])
                result["logcat_all_saved"] = True
        except (subprocess.TimeoutExpired, OSError):
            pass

    try:
        if paths["tombstones"].exists():
            shutil.rmtree(paths["tombstones"], ignore_errors=True)
        pull = run(
            [ADB, "-s", SERIAL, "pull", "/data/tombstones", str(paths["tombstones"])],
            timeout=30, check=False,
        )
        if pull.returncode == 0 and paths["tombstones"].exists():
            result["tombstones_dir"] = str(paths["tombstones"])
            result["tombstones_pulled"] = True
        else:
            probe_text = (
                f"adb pull returncode={pull.returncode}\n"
                f"stdout={pull.stdout or ''}\n"
                f"stderr={pull.stderr or ''}\n"
            )
            try:
                probe = run(
                    [ADB, "-s", SERIAL, "shell", "ls", "-laZ", "/data/tombstones"],
                    timeout=10, check=False,
                )
                probe_text += (
                    "\n===== ls -laZ /data/tombstones =====\n"
                    + (probe.stdout or probe.stderr or "")
                )
            except (subprocess.TimeoutExpired, OSError) as exc:
                probe_text += f"\n<tombstone probe failed: {exc}>\n"
            paths["tombstone_probe"].write_text(
                probe_text,
                encoding="utf-8",
                errors="replace",
            )
            result["tombstone_probe"] = str(paths["tombstone_probe"])
    except (subprocess.TimeoutExpired, OSError) as exc:
        paths["tombstone_probe"].write_text(
            f"<tombstone pull failed: {exc}>\n",
            encoding="utf-8",
            errors="replace",
        )
        result["tombstone_probe"] = str(paths["tombstone_probe"])

    return result


def write_boot_report(extra: dict[str, object] | None = None) -> dict[str, object]:
    RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
    try:
        guest = guest_status()
    except Exception as exc:
        # This report is specifically useful before ADB exists. Never let a
        # missing/offline adb executable hide the serial/QEMU crash evidence.
        guest = {
            "serial": SERIAL,
            "device_state": "unavailable",
            "diagnostic_error": str(exc),
        }
    report = {
        "runtime_root": str(RUNTIME_ROOT),
        "serial": SERIAL,
        "system_image": DEFAULT_SYSTEM_IMAGE,
        "cpu": CPU_MODEL,
        "cpu_cores": CPU_CORES,
        "ram_mb": RAM_MB,
        "gpu": GPU_MODE,
        "machine": google_arm64_machine(),
        "boot_device": RANCHU_BOOT_DEVICE,
        "codec2_disabled": True,
        "guest": guest,
        "boot_crash": collect_boot_crash(),
        "adb_live_state": collect_adb_live_state(),
        "adb_diagnostics": collect_adb_boot_diagnostics(),
        "critical_lines": _qemu_critical_lines(),
    }
    if extra:
        report.update(extra)
    runtime_paths()["boot_report"].write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report


def _qemu_critical_lines():
    log_path = runtime_paths()["stdout"]
    if not log_path.is_file():
        return ""
    needles = (
        "panic", "fatal", "error", "unsupported", "not supported",
        "arm64", "x86_64", "accelerat", "qemu2", "exit", "abort",
    )
    hits = []
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        lower = line.lower()
        if any(needle in lower for needle in needles):
            hits.append(line)
    return "\n".join(hits[-120:])


def _boot_milestone() -> str:
    log_path = runtime_paths()["stdout"]
    if not log_path.is_file():
        return "qemu-start"
    text = log_path.read_text(encoding="utf-8", errors="replace").lower()

    # Only the real property is a boot-complete signal. The previous heuristic
    # accidentally matched lines such as:
    #   setprop sys.bootstat.first_boot_completed 0
    # and messages saying a process crashed "before boot completed".
    if (
        "sys.boot_completed=1" in text
        or re.search(r"setprop\s+sys\.boot_completed\s+1(?:\s|$)", text)
    ):
        return "android-boot-complete"

    adbd = (
        "starting service 'adbd'" in text
        or 'starting service "adbd"' in text
    )
    if "zygote" in text or "app_process64" in text:
        return "zygote+adbd" if adbd else "zygote"
    if "surfaceflinger" in text:
        return "surfaceflinger+adbd" if adbd else "surfaceflinger"
    if "product" in text and "system_ext" in text and "vendor" in text:
        return "logical-partitions-mounted"
    if "super" in text:
        return "super-discovered"
    if "init: second stage" in text or "second stage init" in text:
        return "init-second-stage"
    if "init" in text:
        return "kernel-init"
    return "kernel"


def wait_for_boot(timeout=1200, process=None):
    started = time.monotonic()
    deadline = started + timeout
    next_report = started + 10
    first_device_at = None
    last_state = ""
    while time.monotonic() < deadline:
        if process is not None:
            code = process.poll()
            if code is not None:
                crash = collect_boot_crash()
                write_boot_report({"qemu_exit_code": code})
                crash_hint = (
                    f"\nBoot crash: {runtime_paths()['boot_crash']}"
                    if crash.get("found") else ""
                )
                raise RuntimeError(
                    f"ARM64 QEMU exited before Android boot; exit_code={code}"
                    f"{crash_hint}\n"
                    f"QEMU critical lines:\n{_qemu_critical_lines()}\n\n"
                    f"QEMU log tail:\n{_qemu_log_tail()}"
                )
        try:
            run([ADB, "connect", SERIAL], timeout=3, check=False)
        except subprocess.TimeoutExpired:
            # TCP ADB is unavailable during the slow TCG boot. A missed poll is
            # expected and must not terminate an otherwise healthy QEMU guest.
            time.sleep(3)
            continue
        try:
            state = run([ADB, "-s", SERIAL, "get-state"], timeout=5, check=False)
            last_state = state.stdout.strip()
        except subprocess.TimeoutExpired:
            last_state = "unresponsive"
        now = time.monotonic()
        if last_state == "device":
            if first_device_at is None:
                first_device_at = now
            try:
                boot = adb(
                    "shell", "getprop", "sys.boot_completed",
                    timeout=5, check=False,
                ).stdout.strip()
            except subprocess.TimeoutExpired:
                boot = ""
            if boot == "1":
                print(f"ARM64 Android booted: {SERIAL}", flush=True)
                return

            post_adb = now - first_device_at
            if post_adb >= POST_ADB_STALL_SECONDS:
                stage = _boot_milestone()
                write_boot_report({
                    "adb_state": last_state,
                    "boot_stage": stage,
                    "post_adb_stall_seconds": int(post_adb),
                })
                raise TimeoutError(
                    "ARM64 Android stalled after adbd became reachable; "
                    f"sys.boot_completed is still empty after {int(post_adb)}s "
                    f"(stage={stage}). Diagnostics: "
                    f"{runtime_paths()['boot_report']}"
                )
        else:
            first_device_at = None
        if now >= next_report:
            elapsed = int(now - started)
            print(
                f"ARM64 boot waiting: {elapsed}s | "
                f"adb={last_state or 'missing'} | stage={_boot_milestone()}",
                flush=True,
            )
            next_report = now + 15
        time.sleep(3)
    crash = collect_boot_crash()
    write_boot_report({"timeout_seconds": timeout, "adb_state": last_state})
    crash_hint = (
        f"\nBoot crash: {runtime_paths()['boot_crash']}"
        if crash.get("found") else ""
    )
    raise TimeoutError(
        f"ARM64 guest did not boot; adb_state={last_state!r}"
        f"{crash_hint}\n"
        f"QEMU log tail:\n{_qemu_log_tail()}"
    )


def guest_status() -> dict[str, object]:
    try:
        state = run(
            [ADB, "-s", SERIAL, "get-state"],
            timeout=5,
            check=False,
        ).stdout.strip()
    except subprocess.TimeoutExpired:
        return {
            "serial": SERIAL,
            "device_state": "unresponsive",
            "diagnostic_error": "adb get-state timed out",
        }

    if state != "device":
        return {"serial": SERIAL, "device_state": state or "missing"}

    def safe_shell(*args, timeout=5) -> str:
        try:
            return adb(
                "shell", *args, timeout=timeout, check=False
            ).stdout.strip()
        except subprocess.TimeoutExpired:
            return ""

    # "adb get-state" can become "device" before zygote/framework are usable.
    # First ask only init-level properties. Avoid pm/wm/package probes until
    # sys.boot_completed=1, otherwise diagnostics themselves can hang.
    boot_completed = safe_shell("getprop", "sys.boot_completed", timeout=5)
    props = {
        "serial": SERIAL,
        "device_state": state,
        "boot_completed": boot_completed,
        "android": safe_shell("getprop", "ro.build.version.release"),
        "abi": safe_shell("getprop", "ro.product.cpu.abi"),
        "abilist": safe_shell("getprop", "ro.product.cpu.abilist"),
        "native_bridge": safe_shell("getprop", "ro.dalvik.vm.native.bridge"),
        "codec2": safe_shell("getprop", "debug.stagefright.ccodec"),
        "kernel_codec2": safe_shell("getprop", "ro.kernel.qemu.media.ccodec"),
        "boot_devices": safe_shell("getprop", "ro.boot.boot_devices"),
        "logical_partitions": safe_shell("getprop", "ro.boot.logical_partitions"),
        "vbmeta_device_state": safe_shell("getprop", "ro.boot.vbmeta.device_state"),
        "model": safe_shell("getprop", "ro.product.model"),
        "resolution": "",
        "density": "",
    }
    if boot_completed == "1":
        props["resolution"] = safe_shell("wm", "size")
        props["density"] = safe_shell("wm", "density")
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
            "verify-game", "capture", "boot-report", "stop", "all",
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
        print_json(build_google_ranchu_command(window=args.window, wipe=args.wipe))
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
    elif args.action == "boot-report":
        print_json(write_boot_report())
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
