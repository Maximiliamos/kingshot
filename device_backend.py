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
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import threading
import time
from typing import Any, Iterable

import cv2
import numpy as np


DEFAULT_PACKAGE = "com.got.globalru"
DEFAULT_ACTIVITY = "com.unity3d.player.MyMainPlayerActivity"
WINDOWS_NO_WINDOW = (
    getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
)


class BackendError(RuntimeError):
    pass



def _parse_df_available_mb(output: str, mount: str = "/data") -> int | None:
    """Parse Android/toybox df output even when the filesystem name wraps.

    WSA may print a long /dev/block/... filesystem on its own line and the
    numeric columns on the next line. In that layout a fixed column index on
    the final physical line mistakes Use% for Available and reports 0 MiB.
    Anchor on the requested mount point and its preceding Use% token instead.
    """
    tokens = [token for line in output.splitlines() for token in line.split()]
    mount_indexes = [index for index, token in enumerate(tokens) if token == mount]
    for mount_index in reversed(mount_indexes):
        for percent_index in range(mount_index - 1, max(-1, mount_index - 5), -1):
            token = tokens[percent_index]
            if not token.endswith("%"):
                continue
            available_index = percent_index - 1
            if available_index < 0:
                break
            try:
                available_kb = int(tokens[available_index].replace(",", ""))
            except ValueError:
                break
            return max(0, available_kb // 1024)
    return None


def _parse_statfs_available_mb(available_blocks: str, block_size: str) -> int | None:
    """Convert ``stat -f`` available blocks and block size into MiB."""
    try:
        blocks = int(available_blocks.strip())
        size = int(block_size.strip())
    except (TypeError, ValueError):
        return None
    if blocks < 0 or size <= 0:
        return None
    return (blocks * size) // (1024 * 1024)


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
    data_free_status: str = "not_checked"
    data_free_probe: str = ""

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

    def volume_up(self) -> None:
        self.keyevent("KEYCODE_VOLUME_UP")

    def volume_down(self) -> None:
        self.keyevent("KEYCODE_VOLUME_DOWN")

    def volume_mute(self) -> None:
        self.keyevent("KEYCODE_VOLUME_MUTE")

    def launch_app(self) -> str:
        # Do not use `am start -W` here. Unity/WSA can create the process while
        # keeping ActivityTaskManager's wait-for-draw request blocked for
        # minutes. PID, framebuffer and stability are verified independently
        # immediately after this fire-and-observe launch request.
        return self.shell(
            ["am", "start", "-n", f"{self.package}/{self.activity}"],
            timeout=30,
        )

    def stop_app(self) -> str:
        return self.shell(["am", "force-stop", self.package])

    def clear_app_data(self) -> str:
        return self.shell(["pm", "clear", self.package], timeout=120)

    def grant_runtime_permissions(self) -> dict[str, bool]:
        """Grant deterministic, declared permissions needed after `pm clear`."""
        results = {}
        for permission in ("android.permission.POST_NOTIFICATIONS",):
            self.shell(
                ["pm", "grant", self.package, permission],
                timeout=30,
            )
            results[permission] = True
        return results

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
                creationflags=WINDOWS_NO_WINDOW,
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
            creationflags=WINDOWS_NO_WINDOW,
        )
        return (result.stdout or result.stderr or "").strip()

    def _getprop(self, name: str) -> str:
        return self.shell(["getprop", name], timeout=15).strip()

    def _write_data_free_evidence(self, payload: dict[str, Any]) -> None:
        """Persist the exact free-space probe result for host acceptance reports."""
        debug_dir = Path(os.environ.get("WAR_BOT_DEBUG_DIR", "debug"))
        try:
            debug_dir.mkdir(parents=True, exist_ok=True)
            target = debug_dir / "android-data-free-space.json"
            temporary = target.with_name(
                f"{target.name}.tmp-{os.getpid()}-{threading.get_ident()}"
            )
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temporary, target)
        except OSError:
            # Evidence persistence must not hide the actual health result.
            pass

    def _probe_data_free_space(self) -> tuple[int, str, str]:
        attempts: list[dict[str, Any]] = []

        def run(command: list[str], timeout: int) -> str | None:
            try:
                output = self.shell(command, timeout=timeout)
                attempts.append(
                    {"command": command, "status": "ok", "stdout": output, "stderr": ""}
                )
                return output
            except BackendError as exc:
                attempts.append(
                    {"command": command, "status": "error", "stdout": "", "stderr": str(exc)}
                )
                return None

        # WSA's toybox df may block indefinitely while statfs remains immediate.
        # Query the two stat fields separately because adb argument forwarding
        # does not preserve the space in a combined "%a %S" format string.
        available_blocks = run(["stat", "-f", "-c", "%a", "/data"], timeout=5)
        block_size = run(["stat", "-f", "-c", "%S", "/data"], timeout=5)
        stat_mb = (
            _parse_statfs_available_mb(available_blocks, block_size)
            if available_blocks is not None and block_size is not None
            else None
        )
        if stat_mb is not None:
            status, probe, value = "ok", "statfs", stat_mb
        else:
            df_output = run(["df", "-k", "/data"], timeout=5)
            df_mb = _parse_df_available_mb(df_output, "/data") if df_output is not None else None
            if df_mb is not None:
                status, probe, value = "ok", "df", df_mb
            elif any(item["status"] == "error" for item in attempts):
                status, probe, value = "command_error", "", 0
            else:
                status, probe, value = "unrecognized", "", 0

        self._write_data_free_evidence(
            {
                "schema": 1,
                "checked_at_epoch": time.time(),
                "serial": self.serial,
                "mount": "/data",
                "status": status,
                "probe": probe,
                "data_free_mb": value,
                "attempts": attempts,
            }
        )
        return value, status, probe

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
        data_free_status = "not_checked"
        data_free_probe = ""
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
            data_free_mb, data_free_status, data_free_probe = self._probe_data_free_space()

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
            data_free_status=data_free_status,
            data_free_probe=data_free_probe,
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
                and last.data_free_status == "ok"
                and last.data_free_mb >= 1024
            ):
                return last
            time.sleep(2.0)
        raise BackendError(
            "Android runtime services did not become ready: "
            f"health={last.to_dict()} framebuffer_error={last_frame_error!r}; "
            "requires a successful /data free-space probe and at least 1024 MiB free"
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

    def input_text(self, value: str) -> None:
        if value.isascii():
            super().input_text(value)
            return

        # ADB's `input text` is not Unicode-safe. For manual Cyrillic input,
        # use Android clipboard + paste through the optional structured UI
        # channel. If it is unavailable we fail closed rather than garbling text.
        device = self._uiautomator()
        if device is None:
            raise BackendError(
                "Unicode input requires optional uiautomator2 support; "
                "install requirements-android-optional.txt"
            )
        try:
            device.set_clipboard(value)
            self.keyevent("KEYCODE_PASTE")
        except Exception as exc:
            raise BackendError(f"Unicode clipboard input failed: {exc}") from exc

    def unicode_clipboard_health(self, value: str = "ТугаринMVP") -> dict:
        """Verify the Unicode system-UI channel without pasting into the game."""
        device = self._uiautomator()
        if device is None:
            raise BackendError(
                "Unicode clipboard health requires requirements-android-optional.txt"
            )

        old_value = None
        can_restore = False
        try:
            try:
                old_value = device.clipboard
                can_restore = True
            except Exception:
                old_value = None

            device.set_clipboard(str(value))
            readback = None
            readable = False
            try:
                readback = device.clipboard
                readable = True
            except Exception:
                # Some Android/uiautomator2 combinations expose set-only
                # clipboard access. A successful Cyrillic set still proves the
                # channel used by manual input is available.
                readback = None

            if readable and readback != str(value):
                raise BackendError(
                    f"Unicode clipboard roundtrip mismatch: {readback!r}"
                )
            return {
                "ready": True,
                "readable": readable,
                "roundtrip": (readback == str(value)) if readable else None,
            }
        except BackendError:
            raise
        except Exception as exc:
            raise BackendError(f"Unicode clipboard health failed: {exc}") from exc
        finally:
            if can_restore:
                try:
                    device.set_clipboard(old_value or "")
                except Exception:
                    pass

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
        transport_failures = 0
        empty_pid_checks = 0
        while time.monotonic() < deadline:
            try:
                current = self.shell(["pidof", self.package], timeout=10).strip()
                transport_failures = 0
            except BackendError:
                # WSA can briefly recycle the ADB transport while the guest and
                # game stay alive. Do not turn one transient transport failure
                # into a false "game exited" verdict.
                transport_failures += 1
                if transport_failures >= 4:
                    raise BackendError(
                        f"ADB transport stayed unavailable during the "
                        f"{stability_seconds}s stability gate"
                    )
                time.sleep(2.0)
                continue

            if not current:
                empty_pid_checks += 1
                if empty_pid_checks >= 3:
                    raise BackendError(
                        f"{self.package} exited during the "
                        f"{stability_seconds}s stability gate"
                    )
                time.sleep(2.0)
                continue

            empty_pid_checks = 0
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

    def ui_click_resource(self, resource_id: str, timeout: float = 2.0) -> bool:
        """Click one explicit Android system-control resource id."""
        device = self._uiautomator()
        if device is None:
            return False
        selector = device(resourceId=resource_id)
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

    @staticmethod
    def _window_user32():
        return ctypes.windll.user32

    @staticmethod
    def _game_window():
        """Return the visible Kingshot HWND and its client rectangle."""
        if os.name != "nt":
            return None
        user32 = WsaBackend._window_user32()
        enum_proc = ctypes.WINFUNCTYPE(
            ctypes.c_bool, wintypes.HWND, wintypes.LPARAM
        )
        matches = []

        def visit(hwnd, _):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if not length:
                return True
            title = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, title, length + 1)
            folded = title.value.casefold()
            if "война за трон" not in folded and "kingshot" not in folded:
                return True
            rect = wintypes.RECT()
            origin = wintypes.POINT(0, 0)
            if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
                return True
            if not user32.ClientToScreen(hwnd, ctypes.byref(origin)):
                return True
            width = rect.right - rect.left
            height = rect.bottom - rect.top
            if width >= 100 and height >= 100:
                matches.append((hwnd, origin.x, origin.y, width, height))
            return True

        user32.EnumWindows(enum_proc(visit), 0)
        return matches[0] if len(matches) == 1 else None

    def _post_window_pointer(
        self,
        points: list[tuple[int, int]],
        duration_ms: int = 50,
    ) -> bool:
        """Inject client-relative pointer coordinates into the real WSA HWND.

        The bot and GUI map vision into the Kingshot client surface. Convert
        those client coordinates to desktop pixels only at this final Win32
        boundary, briefly foreground the game so Unity accepts the input, and
        restore the user's cursor/foreground window even on a failed swipe.
        """
        target = self._game_window()
        if target is None or not points:
            return False
        hwnd, left, top, width, height = target
        normalized = [(int(x), int(y)) for x, y in points]
        if any(x < 0 or y < 0 or x >= width or y >= height for x, y in normalized):
            return False

        user32 = self._window_user32()
        old_foreground = user32.GetForegroundWindow()
        old_cursor = wintypes.POINT()
        if not user32.GetCursorPos(ctypes.byref(old_cursor)):
            return False
        if not user32.SetForegroundWindow(hwnd):
            return False

        button_down = False
        try:
            time.sleep(0.15)
            first_x, first_y = normalized[0]
            if not user32.SetCursorPos(left + first_x, top + first_y):
                return False
            user32.mouse_event(0x0002, 0, 0, 0, 0)
            button_down = True

            if len(normalized) > 1:
                delay = max(0.001, duration_ms / 1000.0 / max(1, len(normalized) - 1))
                for x, y in normalized[1:]:
                    time.sleep(delay)
                    if not user32.SetCursorPos(left + x, top + y):
                        return False
            else:
                time.sleep(max(0.03, duration_ms / 1000.0))

            user32.mouse_event(0x0004, 0, 0, 0, 0)
            button_down = False
            time.sleep(0.20)
            return True
        finally:
            if button_down:
                try:
                    user32.mouse_event(0x0004, 0, 0, 0, 0)
                except Exception:
                    pass
            try:
                user32.SetCursorPos(old_cursor.x, old_cursor.y)
            except Exception:
                pass
            if old_foreground and old_foreground != hwnd:
                try:
                    user32.SetForegroundWindow(old_foreground)
                except Exception:
                    pass

    def tap(self, x: int, y: int) -> None:
        if not self._post_window_pointer([(int(x), int(y))]):
            raise BackendError(
                "WSA host-input is unavailable; refusing an ADB tap with "
                "unverified focus/coordinate mapping."
            )

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int) -> None:
        steps = max(4, min(30, int(duration_ms) // 25))
        points = [
            (
                round(x1 + (x2 - x1) * index / steps),
                round(y1 + (y2 - y1) * index / steps),
            )
            for index in range(steps + 1)
        ]
        if not self._post_window_pointer(points, duration_ms):
            raise BackendError(
                "WSA host-input is unavailable; refusing an ADB swipe with "
                "unverified focus/coordinate mapping."
            )

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
            timeout=15,
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
