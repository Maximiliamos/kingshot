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
        result = subprocess.run(
            [*self._base(), *[str(x) for x in args]],
            capture_output=True,
            text=text,
            encoding="utf-8" if text else None,
            errors="replace" if text else None,
            timeout=timeout,
            check=False,
        )
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

    def connect(self) -> str:
        if ":" not in self.serial:
            return ""
        if not self.adb_path.is_file():
            raise BackendError(f"adb.exe not found: {self.adb_path}")
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
            if ":" in self.serial:
                self.connect()
            state = self._run(["get-state"], timeout=10, check=False).stdout.strip()
        except (BackendError, subprocess.TimeoutExpired):
            state = ""
        if state != "device":
            return DeviceHealth(
                backend=self.backend_name,
                serial=self.serial,
                state=state or "missing",
            )

        def prop(name: str) -> str:
            try:
                return self._getprop(name)
            except BackendError:
                return ""

        try:
            running = bool(self.shell(["pidof", self.package], timeout=10).strip())
        except BackendError:
            running = False
        try:
            resolution = self.shell(["wm", "size"], timeout=10).strip()
        except BackendError:
            resolution = ""

        return DeviceHealth(
            backend=self.backend_name,
            serial=self.serial,
            state=state,
            boot_completed=prop("sys.boot_completed"),
            android=prop("ro.build.version.release"),
            abi=prop("ro.product.cpu.abi"),
            abilist=prop("ro.product.cpu.abilist"),
            native_bridge=prop("ro.dalvik.vm.native.bridge"),
            package_running=running,
            model=prop("ro.product.model"),
            resolution=resolution,
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
        return native_arm64_poc.start_direct(window=window, wipe=wipe, wait=True)

    def stop_runtime(self) -> None:
        import native_arm64_poc
        native_arm64_poc.stop()

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
    selected = (name or os.environ.get("WAR_BOT_BACKEND", "native_arm64")).strip().lower()
    if selected in ("native", "native_arm64", "emulator"):
        return NativeArm64Backend(serial=serial, adb_path=adb_path)
    if selected in ("adb", "android"):
        return AdbDeviceBackend(serial=serial, adb_path=adb_path)
    raise BackendError(
        f"Unknown Android backend {selected!r}; expected native_arm64 or adb"
    )
