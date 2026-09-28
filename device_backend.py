"""Unified Android device backend for WAR BOT.

Separates Android transport from the game state machine.

Design ideas:
- adbutils: one device-scoped ADB transport with explicit serial;
- uiautomator2: optional higher-level UI channel for Android system dialogs;
- scrcpy: keep video/control transport independent from game logic;
- Airtest: keep Unity/game UI image-driven with OpenCV.

This module does not patch APKs, hide virtualization, bypass Play Integrity,
or alter game native libraries.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
import os
from pathlib import Path
import subprocess
import time
from typing import Any, Iterable

import cv2
import numpy as np


DEFAULT_PACKAGE = "com.got.globalru"
DEFAULT_ACTIVITY = "com.unity3d.player.MyMainPlayerActivity"


class BackendError(RuntimeError):
    pass


@dataclass(frozen=True)
class DeviceHealth:
    backend: str
    serial: str
    state: str
    boot_completed: str = ""
    android: str = ""
    abi: str = ""
    abilist: str = ""
    native_bridge: str = ""
    package_running: bool = False
    model: str = ""
    resolution: str = ""
    network_ready: bool = False
    internet_reachable: bool = False
    audio_service_ready: bool = False
    package_manager_ready: bool = False
    data_free_mb: int = 0

    @property
    def ready(self) -> bool:
        return self.state == "device" and self.boot_completed == "1"

    @property
    def native_arm64(self) -> bool:
        bridge = self.native_bridge.strip().lower()
        return (
            self.abi == "arm64-v8a"
            and "x86" not in self.abilist.lower()
            and bridge in ("", "0", "none")
        )

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["ready"] = self.ready
        value["native_arm64"] = self.native_arm64
        return value


class DeviceBackend(ABC):
    backend_name = "abstract"

    def __init__(
        self,
        *,
        serial: str,
        package: str = DEFAULT_PACKAGE,
        activity: str = DEFAULT_ACTIVITY,
    ):
        self.serial = serial
        self.package = package
        self.activity = activity

    @abstractmethod
    def health(self) -> DeviceHealth:
        raise NotImplementedError

    @abstractmethod
    def frame(self) -> np.ndarray:
        raise NotImplementedError

    @abstractmethod
    def tap(self, x: int, y: int) -> None:
        raise NotImplementedError

    @abstractmethod
    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int) -> None:
        raise NotImplementedError

    @abstractmethod
    def keyevent(self, code: int | str) -> None:
        raise NotImplementedError

    @abstractmethod
    def shell(self, args: Iterable[str] | str, *, timeout: int = 60) -> str:
        raise NotImplementedError

    def hold(self, x: int, y: int, duration_ms: int) -> None:
        self.swipe(x, y, x, y, duration_ms)

    def input_text(self, value: str) -> None:
        if not value.isascii():
            raise BackendError(
                "ADB input text is ASCII-only in WAR BOT; use the verified "
                "Unicode/on-screen-keyboard path for Cyrillic input."
            )
        self.shell(["input", "text", value])

    def launch_app(self) -> str:
        return self.shell(
            ["am", "start", "-W", "-n", f"{self.package}/{self.activity}"],
            timeout=120,
        )

    def stop_app(self) -> str:
        return self.shell(["am", "force-stop", self.package])

    def clear_app_data(self) -> str:
        return self.shell(["pm", "clear", self.package], timeout=120)

    def close(self) -> None:
        pass

    def require_ready(self, *, native_arm64: bool = False) -> DeviceHealth:
        health = self.health()
        if not health.ready:
            raise BackendError(
                f"Android is not ready: backend={health.backend} "
                f"serial={health.serial} state={health.state} "
                f"boot_completed={health.boot_completed!r}"
            )
        if native_arm64 and not health.native_arm64:
            raise BackendError(
                "Native ARM64 gate failed: "
                f"abi={health.abi!r} abilist={health.abilist!r} "
                f"native_bridge={health.native_bridge!r}"
            )
        return health


class AdbDeviceBackend(DeviceBackend):
    backend_name = "adb"

    def __init__(
        self,
        *,
        serial: str | None = None,
        adb_path: str | os.PathLike[str] | None = None,
        package: str = DEFAULT_PACKAGE,
        activity: str = DEFAULT_ACTIVITY,
    ):
        serial = serial or os.environ.get("WAR_BOT_ANDROID_SERIAL", "127.0.0.1:5561")
        super().__init__(serial=serial, package=package, activity=activity)
        configured = adb_path or os.environ.get("WAR_BOT_ADB")
        if configured:
            self.adb_path = Path(configured)
        else:
            candidates = (
                Path(r"C:\Android\Sdk\platform-tools\adb.exe"),
                Path(r"C:\platform-tools\adb.exe"),
            )
            self.adb_path = next((p for p in candidates if p.is_file()), candidates[0])
        self._u2 = None
        self._u2_attempted = False
        self._last_connect_attempt = 0.0

    def _base(self) -> list[str]:
        return [str(self.adb_path), "-s", self.serial]

    def _run(
        self,
        args: Iterable[str],
        *,
        timeout: int = 60,
        check: bool = True,
        text: bool = True,
    ) -> subprocess.CompletedProcess:
        if not self.adb_path.is_file():
            raise BackendError(f"adb.exe not found: {self.adb_path}")
        self._ensure_transport()
        command = [*self._base(), *[str(x) for x in args]]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=text,
                encoding="utf-8" if text else None,
                errors="replace" if text else None,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            detail = " ".join(str(x) for x in args)
            raise BackendError(
                f"ADB timeout after {timeout}s on {self.serial}: {detail}"
            ) from exc
        if check and result.returncode:
            if text:
                detail = (result.stderr or result.stdout or "").strip()
            else:
                detail = bytes(result.stderr or b"")[:200].decode("utf-8", "replace")
            raise BackendError(
                f"ADB command failed ({result.returncode}) on {self.serial}: "
                f"{detail or 'no diagnostic output'}"
            )
        return result

    def run_adb(
        self,
        args: Iterable[str],
        *,
        timeout: int = 60,
        check: bool = False,
    ) -> subprocess.CompletedProcess:
        return self._run(args, timeout=timeout, check=check, text=True)

    def _ensure_transport(self) -> None:
        if ":" not in self.serial:
            return
        now = time.monotonic()
        if now - self._last_connect_attempt < 2.0:
            return
        self._last_connect_attempt = now
        self.connect()

    def connect(self) -> str:
        if ":" not in self.serial:
            return ""
        if not self.adb_path.is_file():
            raise BackendError(f"adb.exe not found: {self.adb_path}")
        self._last_connect_attempt = time.monotonic()
        result = subprocess.run(
            [str(self.adb_path), "connect", self.serial],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
        )
        return (result.stdout or result.stderr or "").strip()

    def _getprop(self, name: str) -> str:
        return self.shell(["getprop", name], timeout=15).strip()

    def health(self) -> DeviceHealth:
        try:
            state = self._run(["get-state"], timeout=5, check=False).stdout.strip()
        except BackendError:
            state = ""
        if state != "device":
            return DeviceHealth(
                backend=self.backend_name,
                serial=self.serial,
                state=state or "missing",
            )

        def prop(name: str, timeout: int = 5) -> str:
            try:
                return self.shell(["getprop", name], timeout=timeout).strip()
            except BackendError:
                return ""

        # ADB may report "device" while Android userspace is still blocked.
        # Do not issue package-manager / wm probes until init has explicitly
        # reached sys.boot_completed=1; those commands can hang pre-zygote.
        boot_completed = prop("sys.boot_completed", timeout=5)
        android = prop("ro.build.version.release", timeout=5)
        abi = prop("ro.product.cpu.abi", timeout=5)
        abilist = prop("ro.product.cpu.abilist", timeout=5)
        native_bridge = prop("ro.dalvik.vm.native.bridge", timeout=5)
        model = prop("ro.product.model", timeout=5)

        running = False
        resolution = ""
        network_ready = False
        internet_reachable = False
        audio_service_ready = False
        package_manager_ready = False
        data_free_mb = 0
        if boot_completed == "1":
            try:
                running = bool(self.shell(["pidof", self.package], timeout=5).strip())
            except BackendError:
                running = False
            try:
                resolution = self.shell(["wm", "size"], timeout=5).strip()
            except BackendError:
                resolution = ""

            try:
                package_manager_ready = bool(
                    self.shell(["pm", "path", "com.android.settings"], timeout=8).strip()
                )
            except BackendError:
                package_manager_ready = False
            try:
                df_output = self.shell(["df", "-k", "/data"], timeout=8)
                df_lines = [line.split() for line in df_output.splitlines() if line.strip()]
                if len(df_lines) >= 2 and len(df_lines[-1]) >= 4:
                    data_free_mb = max(0, int(df_lines[-1][3]) // 1024)
            except (BackendError, ValueError, IndexError):
                data_free_mb = 0

            # Runtime service probes are intentionally read-only. WSA can
            # expose a fully usable virtual Ethernet connection even when
            # `ip route` is incomplete/empty for the shell user, so Android's
            # ConnectivityService is the primary source of truth.
            connectivity = ""
            try:
                connectivity = self.shell(["dumpsys", "connectivity"], timeout=8)
            except BackendError:
                connectivity = ""

            if connectivity:
                upper = connectivity.upper()
                active_index = upper.find("ACTIVE DEFAULT NETWORK:")
                active = upper[active_index:active_index + 5000] if active_index >= 0 else ""
                network_ready = (
                    active_index >= 0
                    and "CONNECTED" in active
                    and ("ETHERNET" in active or "WIFI" in active or "CELLULAR" in active)
                )
                internet_reachable = (
                    network_ready
                    and "INTERNET" in active
                    and "VALIDATED" in active
                )

            # Route inspection is only a fallback/supplement for Android
            # builds where dumpsys connectivity is unavailable or abbreviated.
            try:
                routes = self.shell(["ip", "route"], timeout=5)
                if "default" in routes.lower():
                    network_ready = True
            except BackendError:
                pass

            # ICMP is a final fallback only; some networks block it.
            if network_ready and not internet_reachable:
                try:
                    ping = self._run(
                        ["shell", "ping", "-c", "1", "-W", "2", "1.1.1.1"],
                        timeout=5,
                        check=False,
                        text=True,
                    )
                    internet_reachable = ping.returncode == 0
                except BackendError:
                    internet_reachable = False
            try:
                audio = self.shell(["dumpsys", "audio"], timeout=8)
                audio_service_ready = bool(audio.strip()) and (
                    "STREAM_MUSIC" in audio or "Audio routes" in audio or "AudioService" in audio
                )
            except BackendError:
                audio_service_ready = False

        return DeviceHealth(
            backend=self.backend_name,
            serial=self.serial,
            state=state,
            boot_completed=boot_completed,
            android=android,
            abi=abi,
            abilist=abilist,
            native_bridge=native_bridge,
            package_running=running,
            model=model,
            resolution=resolution,
            network_ready=network_ready,
            internet_reachable=internet_reachable,
            audio_service_ready=audio_service_ready,
            package_manager_ready=package_manager_ready,
            data_free_mb=data_free_mb,
        )

    def wait_ready(self, timeout: int = 180) -> DeviceHealth:
        deadline = time.monotonic() + timeout
        last = self.health()
        while time.monotonic() < deadline:
            last = self.health()
            if last.ready:
                return last
            time.sleep(1.0)
        raise BackendError(
            f"Android did not become ready in {timeout}s: {last.to_dict()}"
        )

    def wait_runtime_services(self, timeout: int = 90) -> DeviceHealth:
        """Wait until the runtime has framebuffer/network/Internet/audio.

        P0 acceptance is stricter than a mere sys.boot_completed=1: Kingshot
        needs a usable display, validated networking and Android's audio
        service. The framebuffer itself is verified with a real screencap.
        """
        deadline = time.monotonic() + max(1, int(timeout))
        last = self.health()
        last_frame_error = ""
        while time.monotonic() < deadline:
            last = self.health()
            frame_ok = False
            if last.ready:
                try:
                    frame = self.frame()
                    frame_ok = bool(frame is not None and frame.size > 0)
                    last_frame_error = ""
                except BackendError as exc:
                    last_frame_error = str(exc)
            if (
                last.ready
                and frame_ok
                and last.network_ready
                and last.internet_reachable
                and last.audio_service_ready
                and last.package_manager_ready
                and last.data_free_mb >= 1024
            ):
                return last
            time.sleep(2.0)
        raise BackendError(
            "Android runtime services did not become ready: "
            f"health={last.to_dict()} framebuffer_error={last_frame_error!r}; "
            "requires package manager and at least 1024 MiB free in /data"
        )

    def frame(self) -> np.ndarray:
        result = self._run(
            ["exec-out", "screencap", "-p"],
            timeout=30,
            check=True,
            text=False,
        )
        raw = bytes(result.stdout or b"")
        if not raw.startswith(b"\x89PNG"):
            raise BackendError(
                f"screencap returned invalid data on {self.serial} ({len(raw)} bytes)"
            )
        image = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None or image.size == 0:
            raise BackendError("OpenCV could not decode Android screencap")
        return image

    def tap(self, x: int, y: int) -> None:
        self.shell(["input", "tap", str(int(x)), str(int(y))])

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int) -> None:
        self.shell([
            "input", "swipe",
            str(int(x1)), str(int(y1)), str(int(x2)), str(int(y2)),
            str(int(duration_ms)),
        ])

    def keyevent(self, code: int | str) -> None:
        self.shell(["input", "keyevent", str(code)])

    def shell(self, args: Iterable[str] | str, *, timeout: int = 60) -> str:
        if isinstance(args, str):
            cmd = ["shell", args]
        else:
            cmd = ["shell", *[str(x) for x in args]]
        return self._run(cmd, timeout=timeout, check=True, text=True).stdout

    def install_apks(self, paths: Iterable[str | os.PathLike[str]]) -> str:
        files = [Path(path) for path in paths]
        missing = [str(path) for path in files if not path.is_file()]
        if missing:
            raise BackendError("APK split(s) not found: " + ", ".join(missing))
        result = self._run(
            ["install-multiple", "-r", *[str(path) for path in files]],
            timeout=300,
            check=True,
            text=True,
        )
        return (result.stdout or result.stderr or "").strip()

    def package_installed(self) -> bool:
        try:
            output = self.shell(["pm", "path", self.package], timeout=20)
        except BackendError:
            return False
        return any(line.startswith("package:") for line in output.splitlines())

    def wait_package_running(self, timeout: int = 60) -> str:
        deadline = time.monotonic() + timeout
        last = ""
        while time.monotonic() < deadline:
            try:
                last = self.shell(["pidof", self.package], timeout=10).strip()
            except BackendError:
                last = ""
            if last:
                return last
            time.sleep(1.0)
        raise BackendError(
            f"{self.package} did not stay running within {timeout}s"
        )

    def wait_package_stable(
        self,
        stability_seconds: int = 45,
        *,
        expected_pid: str | None = None,
    ) -> str:
        pid = expected_pid or self.wait_package_running(timeout=90)
        deadline = time.monotonic() + max(1, stability_seconds)
        while time.monotonic() < deadline:
            try:
                current = self.shell(["pidof", self.package], timeout=10).strip()
            except BackendError:
                current = ""
            if not current:
                raise BackendError(
                    f"{self.package} exited during the {stability_seconds}s stability gate"
                )
            if current != pid:
                raise BackendError(
                    f"{self.package} restarted during stability gate: {pid} -> {current}"
                )
            time.sleep(2.0)
        return pid

    def _uiautomator(self):
        if self._u2_attempted:
            return self._u2
        self._u2_attempted = True
        try:
            import uiautomator2 as u2  # type: ignore
            self._u2 = u2.connect(self.serial)
        except Exception:
            self._u2 = None
        return self._u2

    def ui_dump(self) -> str:
        device = self._uiautomator()
        if device is None:
            raise BackendError("uiautomator2 is not installed or unavailable")
        return device.dump_hierarchy()

    def ui_click_text(self, text: str, timeout: float = 2.0) -> bool:
        device = self._uiautomator()
        if device is None:
            return False
        selector = device(text=text)
        if not selector.wait(timeout=timeout):
            return False
        selector.click()
        return True


class NativeArm64Backend(AdbDeviceBackend):
    backend_name = "native_arm64"

    def __init__(self, **kwargs):
        if "serial" not in kwargs or kwargs["serial"] is None:
            try:
                import native_arm64_poc
                kwargs["serial"] = native_arm64_poc.SERIAL
            except Exception:
                kwargs["serial"] = "127.0.0.1:5561"
        super().__init__(**kwargs)

    def start_runtime(self, *, wipe: bool = False, window: bool = False) -> int:
        import native_arm64_poc
        # Idempotent: returns immediately when the verified ARM64 Android 11
        # image already exists, otherwise installs it through sdkmanager.
        native_arm64_poc.install_system_image()
        return native_arm64_poc.start_direct(window=window, wipe=wipe, wait=True)

    def stop_runtime(self) -> None:
        import native_arm64_poc
        native_arm64_poc.stop()

    def install_verified_game(self, apks_dir: str | os.PathLike[str] | None = None) -> str:
        self.require_ready(native_arm64=True)
        root = Path(
            apks_dir
            or os.environ.get("WAR_BOT_APKS_DIR", r"C:\warbot_emulator_poc\apks_1.12.10")
        )
        names = (
            "base.apk",
            "split_config.arm64_v8a.apk",
            "split_game_asset.apk",
        )
        return self.install_apks(root / name for name in names)

    def collect_game_crash(self) -> Path:
        import native_arm64_poc
        result = self._run(
            ["logcat", "-b", "crash", "-d", "-v", "threadtime"],
            timeout=120,
            check=False,
            text=True,
        )
        path = native_arm64_poc.runtime_paths()["crash"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(result.stdout or result.stderr or "", encoding="utf-8")
        return path

    def health(self) -> DeviceHealth:
        base = super().health()
        data = asdict(base)
        data["backend"] = self.backend_name
        return DeviceHealth(**data)


class WsaBackend(AdbDeviceBackend):
    """Windows Subsystem for Android transport.

    WSA on an x86-64 Windows host may run ARM app libraries through an Android
    native bridge (for example libhoudini). Unlike NativeArm64Backend, this
    backend intentionally does not enforce the no-translation gate: the PoC is
    meant to determine whether WSA's translation and graphics stack can run the
    game stably enough for WAR BOT.
    """

    backend_name = "wsa"

    def __init__(self, **kwargs):
        if "serial" not in kwargs or kwargs["serial"] is None:
            kwargs["serial"] = os.environ.get(
                "WAR_BOT_WSA_SERIAL",
                "127.0.0.1:58526",
            )
        super().__init__(**kwargs)

    @property
    def runtime_root(self) -> Path:
        return Path(os.environ.get(
            "WAR_BOT_WSA_RUNTIME",
            r"C:\warbot_wsa_runtime",
        ))

    def start_runtime(self, *, wipe: bool = False, window: bool = True) -> None:
        # WSA is managed by Windows/Hyper-V rather than WAR BOT. Opening the
        # Settings app is a safe way to wake the subsystem and expose Developer
        # mode / ADB. Never delete WSA userdata from this backend.
        app = (
            r"shell:AppsFolder\MicrosoftCorporationII."
            r"WindowsSubsystemForAndroid_8wekyb3d8bbwe!SettingsApp"
        )
        subprocess.Popen(
            ["explorer.exe", app],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def install_verified_game(
        self,
        apks_dir: str | os.PathLike[str] | None = None,
    ) -> str:
        self.require_ready(native_arm64=False)
        root = Path(
            apks_dir
            or os.environ.get(
                "WAR_BOT_APKS_DIR",
                r"C:\warbot_emulator_poc\apks_1.12.10",
            )
        )
        names = (
            "base.apk",
            "split_config.arm64_v8a.apk",
            "split_game_asset.apk",
        )
        return self.install_apks(root / name for name in names)

    def collect_game_crash(self) -> Path:
        result = self._run(
            ["logcat", "-b", "crash", "-d", "-v", "threadtime"],
            timeout=120,
            check=False,
            text=True,
        )
        path = self.runtime_root / "game-crash.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            result.stdout or result.stderr or "",
            encoding="utf-8",
            errors="replace",
        )
        return path

    def health(self) -> DeviceHealth:
        base = super().health()
        data = asdict(base)
        data["backend"] = self.backend_name
        return DeviceHealth(**data)


class BackendCapture:
    """Adapter exposing the old grab shape to the vision loop."""

    def __init__(self, backend: DeviceBackend):
        self.backend = backend

    def grab(self):
        frame = self.backend.frame()
        height, width = frame.shape[:2]
        return (
            frame,
            f"{self.backend.backend_name}:{self.backend.serial}",
            {"left": 0, "top": 0, "width": width, "height": height},
        )

    def close(self):
        self.backend.close()


def create_backend(
    name: str | None = None,
    *,
    serial: str | None = None,
    adb_path: str | os.PathLike[str] | None = None,
) -> DeviceBackend:
    selected = (name or os.environ.get("WAR_BOT_BACKEND", "wsa")).strip().lower()
    if selected in ("native", "native_arm64", "emulator"):
        return NativeArm64Backend(serial=serial, adb_path=adb_path)
    if selected in ("wsa", "windows_subsystem_android"):
        return WsaBackend(serial=serial, adb_path=adb_path)
    if selected in ("adb", "android"):
        return AdbDeviceBackend(serial=serial, adb_path=adb_path)
    raise BackendError(
        f"Unknown Android backend {selected!r}; expected native_arm64, wsa or adb"
    )
