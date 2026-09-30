"""Low-latency WSA video transport backed by the scrcpy Android server.

Kingshot still runs inside Windows Subsystem for Android.  This module only
replaces Android's old screenrecord CLI, which cannot stream H.264 to stdout on
our WSA build.  The pinned scrcpy server runs as the Android shell user inside
the same WSA instance and exposes a raw H.264 stream through an ADB forward.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import queue
import secrets
import re
import shutil
import socket
import subprocess
import threading
import time

import numpy as np

from device_backend import BackendError


WINDOWS_NO_WINDOW = (
    getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
)

SCRCPY_VERSION = "4.1"
SCRCPY_SERVER_SHA256 = (
    "deacb991ed2509715160ffdc7907e47b4160eb30d1566217e9047fd5b8850cae"
)
SCRCPY_REMOTE_PATH = "/data/local/tmp/tugarin-scrcpy-server-v4.1.jar"


def default_server_path() -> Path:
    override = os.environ.get("TUGARIN_SCRCPY_SERVER")
    if override:
        return Path(override)
    return Path(__file__).resolve().parent / "runtime-tools" / "scrcpy-server-v4.1"


class ScrcpyServerCapture:
    """Receive raw H.264 from scrcpy-server running inside WSA."""

    transport_name = "scrcpy-h264"

    def __init__(
        self,
        backend,
        *,
        server_path: str | os.PathLike[str] | None = None,
        ffmpeg_path: str | None = None,
        max_size: int = 960,
        bit_rate: int = 2_000_000,
        max_fps: int = 15,
        frame_timeout: float = 1.5,
    ):
        self.backend = backend
        self.server_path = Path(server_path or default_server_path())
        if not self.server_path.is_file():
            raise BackendError(
                "Pinned scrcpy-server is missing. Run "
                "scripts/provision_scrcpy_server.ps1 first: "
                f"{self.server_path}"
            )
        actual_sha = hashlib.sha256(self.server_path.read_bytes()).hexdigest()
        if actual_sha.lower() != SCRCPY_SERVER_SHA256:
            raise BackendError(
                "scrcpy-server SHA-256 mismatch: "
                f"expected={SCRCPY_SERVER_SHA256} actual={actual_sha}"
            )

        self.ffmpeg_path = (
            ffmpeg_path
            or os.environ.get("TUGARIN_FFMPEG")
            or shutil.which("ffmpeg.exe")
            or shutil.which("ffmpeg")
        )
        if not self.ffmpeg_path:
            raise BackendError("FFmpeg is unavailable for scrcpy H.264 preview.")

        adb_path = getattr(backend, "adb_path", None)
        if not adb_path:
            raise BackendError("scrcpy preview requires an ADB-backed backend.")
        self.adb_path = str(adb_path)
        self.serial = str(getattr(backend, "serial", "") or "")
        if not self.serial:
            raise BackendError("scrcpy preview requires an ADB serial.")

        health = backend.health()
        if not health.ready:
            raise BackendError("Android is not ready for scrcpy preview.")
        match = re.search(r"(\d+)\s*x\s*(\d+)", health.resolution or "")
        if not match:
            raise BackendError(
                f"Could not determine Android framebuffer size: {health.resolution!r}"
            )
        self.source_width = int(match.group(1))
        self.source_height = int(match.group(2))
        self.max_size = max(320, int(max_size))
        self.bit_rate = max(500_000, int(bit_rate))
        self.scid = secrets.randbelow(0x7FFFFFFE) + 1
        self.socket_name = f"scrcpy_{self.scid:08x}"
        self.max_fps = max(1, int(max_fps))
        self.frame_timeout = max(0.5, float(frame_timeout))
        self.width, self.height = self._fit_size(
            self.source_width, self.source_height, self.max_size
        )

        self._forward_port: int | None = None
        self._server_process = None
        self._ffmpeg_process = None
        self._reader_thread = None
        self._stderr_threads: list[threading.Thread] = []
        self._reader_stop = threading.Event()
        self._frame_queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=1)
        self._server_stderr = ""
        self._server_stdout = ""
        self._ffmpeg_stderr = ""
        self._reader_error = ""
        self._closed = False
        self._start_pipeline()

    @staticmethod
    def _fit_size(width: int, height: int, max_size: int) -> tuple[int, int]:
        width = max(2, int(width))
        height = max(2, int(height))
        scale = min(1.0, float(max_size) / max(width, height))
        out_w = max(2, int(round(width * scale)))
        out_h = max(2, int(round(height * scale)))
        out_w -= out_w % 2
        out_h -= out_h % 2
        return max(2, out_w), max(2, out_h)

    @staticmethod
    def _reserve_port() -> int:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])
        finally:
            sock.close()

    def _adb_run(
        self,
        args: list[str],
        *,
        timeout: float = 30,
        check: bool = True,
    ) -> subprocess.CompletedProcess:
        result = subprocess.run(
            [self.adb_path, "-s", self.serial, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            creationflags=WINDOWS_NO_WINDOW,
        )
        if check and result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise BackendError(
                f"ADB {' '.join(args[:2])} failed ({result.returncode}): {detail}"
            )
        return result

    def _drain_process_stream(self, process, attribute: str, stream_name: str) -> None:
        stream = getattr(process, stream_name, None)
        if stream is None:
            return
        chunks: list[str] = []
        try:
            while not self._reader_stop.is_set():
                line = stream.readline()
                if not line:
                    break
                if isinstance(line, bytes):
                    line = line.decode("utf-8", errors="replace")
                chunks.append(str(line))
                joined = "".join(chunks)
                if len(joined) > 12000:
                    joined = joined[-12000:]
                    chunks = [joined]
                setattr(self, attribute, joined)
        except Exception as exc:
            current = getattr(self, attribute, "")
            setattr(self, attribute, (current + f"\nstderr-reader: {exc}")[-12000:])

    def _start_pipeline(self) -> None:
        self._stop_pipeline()
        if self._closed:
            raise BackendError("scrcpy preview is closed.")

        # The server is an official pinned artifact. Push is cheap (~0.7 MiB)
        # and removes ambiguity about stale versions inside WSA.
        self._adb_run(
            ["push", str(self.server_path), SCRCPY_REMOTE_PATH],
            timeout=30,
            check=True,
        )

        port = self._reserve_port()
        self._adb_run(
            ["forward", f"tcp:{port}", f"localabstract:{self.socket_name}"],
            timeout=10,
            check=True,
        )
        self._forward_port = port

        server_cmd = (
            f"CLASSPATH={SCRCPY_REMOTE_PATH} "
            "app_process / com.genymobile.scrcpy.Server "
            f"{SCRCPY_VERSION} "
            f"scid={self.scid:08x} "
            "tunnel_forward=true audio=false control=false cleanup=false "
            "raw_stream=true video_codec=h264 "
            f"max_size={self.max_size} video_bit_rate={self.bit_rate} "
            f"max_fps={self.max_fps}"
        )
        self._server_process = subprocess.Popen(
            [self.adb_path, "-s", self.serial, "shell", server_cmd],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=WINDOWS_NO_WINDOW,
            bufsize=0,
        )
        # Give app_process a brief head start to bind localabstract:scrcpy.
        # The server then blocks waiting for the host TCP client.
        time.sleep(0.20)

        # The raw_stream option deliberately removes scrcpy protocol headers,
        # so FFmpeg can consume the socket as ordinary H.264.
        ffmpeg_cmd = [
            str(self.ffmpeg_path),
            "-hide_banner",
            "-loglevel", "error",
            "-fflags", "nobuffer+discardcorrupt",
            "-flags", "low_delay",
            "-probesize", "32768",
            "-analyzeduration", "0",
            "-f", "h264",
            "-i", f"tcp://127.0.0.1:{port}?tcp_nodelay=1",
            "-an",
            "-vf", f"scale={self.width}:{self.height}:flags=fast_bilinear",
            "-fps_mode", "passthrough",
            "-f", "rawvideo",
            "-pix_fmt", "bgr24",
            "pipe:1",
        ]
        self._ffmpeg_process = subprocess.Popen(
            ffmpeg_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=WINDOWS_NO_WINDOW,
            bufsize=0,
        )
        if self._ffmpeg_process.stdout is None:
            self._stop_pipeline()
            raise BackendError("FFmpeg did not expose scrcpy decoded-video stdout.")

        self._reader_stop.clear()
        self._frame_queue = queue.Queue(maxsize=1)
        self._reader_error = ""
        self._server_stderr = ""
        self._server_stdout = ""
        self._ffmpeg_stderr = ""

        for process, attr, name, stream_name in (
            (self._server_process, "_server_stdout", "tugarin-scrcpy-server-stdout", "stdout"),
            (self._server_process, "_server_stderr", "tugarin-scrcpy-server-stderr", "stderr"),
            (self._ffmpeg_process, "_ffmpeg_stderr", "tugarin-scrcpy-ffmpeg-stderr", "stderr"),
        ):
            thread = threading.Thread(
                target=self._drain_process_stream,
                args=(process, attr, stream_name),
                name=name,
                daemon=True,
            )
            thread.start()
            self._stderr_threads.append(thread)

        self._reader_thread = threading.Thread(
            target=self._reader_loop,
            name="tugarin-scrcpy-frame-reader",
            daemon=True,
        )
        self._reader_thread.start()

    @staticmethod
    def _read_exact(stream, size: int) -> bytes:
        chunks = bytearray()
        while len(chunks) < size:
            chunk = stream.read(size - len(chunks))
            if not chunk:
                break
            chunks.extend(chunk)
        return bytes(chunks)

    def _reader_loop(self) -> None:
        process = self._ffmpeg_process
        if process is None or process.stdout is None:
            self._reader_error = "FFmpeg scrcpy decoder is not running."
            return

        frame_bytes = self.width * self.height * 3
        try:
            while not self._reader_stop.is_set():
                raw = self._read_exact(process.stdout, frame_bytes)
                if len(raw) != frame_bytes:
                    self._reader_error = (
                        "scrcpy decoder stream ended mid-frame: "
                        f"{len(raw)}/{frame_bytes} bytes"
                    )
                    return
                frame = np.frombuffer(raw, dtype=np.uint8).reshape(
                    (self.height, self.width, 3)
                ).copy()
                try:
                    self._frame_queue.put_nowait(frame)
                except queue.Full:
                    try:
                        self._frame_queue.get_nowait()
                    except queue.Empty:
                        pass
                    try:
                        self._frame_queue.put_nowait(frame)
                    except queue.Full:
                        pass
        except Exception as exc:
            self._reader_error = f"scrcpy frame reader failed: {exc}"

    def _failure_detail(self) -> str:
        details = []
        if self._reader_error:
            details.append(self._reader_error)
        if self._server_process is not None and self._server_process.poll() is not None:
            details.append(f"server_exit={self._server_process.returncode}")
        if self._ffmpeg_process is not None and self._ffmpeg_process.poll() is not None:
            details.append(f"ffmpeg_exit={self._ffmpeg_process.returncode}")
        if self._server_stderr.strip():
            details.append("server_stderr=" + self._server_stderr.strip()[-2000:])
        if self._server_stdout.strip():
            details.append("server_stdout=" + self._server_stdout.strip()[-2000:])
        if self._ffmpeg_stderr.strip():
            details.append("ffmpeg_stderr=" + self._ffmpeg_stderr.strip()[-2000:])
        return " | ".join(details) or "no frame arrived before timeout"

    def _read_frame(self) -> np.ndarray:
        try:
            return self._frame_queue.get(timeout=self.frame_timeout)
        except queue.Empty as exc:
            raise BackendError(
                "scrcpy H.264 preview stalled: " + self._failure_detail()
            ) from exc

    def grab(self):
        # Fail once and let FallbackCapture demote.  Restarting a WSA codec
        # that is known to produce no frames doubles the black-screen delay
        # and briefly runs another encoder while Kingshot is starting.
        frame = self._read_frame()
        return (
            frame,
            f"scrcpy-h264:{self.serial}",
            {
                "left": 0,
                "top": 0,
                "width": self.source_width,
                "height": self.source_height,
            },
        )

    def diagnostics(self) -> dict:
        return {
            "transport": self.transport_name,
            "server_version": SCRCPY_VERSION,
            "server_path": str(self.server_path),
            "server_exists": self.server_path.is_file(),
            "serial": self.serial,
            "forward_port": self._forward_port,
            "scid": f"{self.scid:08x}",
            "socket_name": self.socket_name,
            "source_size": [self.source_width, self.source_height],
            "decoded_size": [self.width, self.height],
            "server_exit": (
                None if self._server_process is None else self._server_process.poll()
            ),
            "ffmpeg_exit": (
                None if self._ffmpeg_process is None else self._ffmpeg_process.poll()
            ),
            "reader_error": self._reader_error,
            "server_stderr": self._server_stderr[-4000:],
            "server_stdout": self._server_stdout[-4000:],
            "ffmpeg_stderr": self._ffmpeg_stderr[-4000:],
        }

    def _stop_pipeline(self) -> None:
        if not hasattr(self, "_reader_stop"):
            return
        self._reader_stop.set()

        for process in (
            getattr(self, "_ffmpeg_process", None),
            getattr(self, "_server_process", None),
        ):
            if process is None:
                continue
            try:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=1.5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=1.0)
            except (OSError, subprocess.SubprocessError):
                pass

        reader = getattr(self, "_reader_thread", None)
        if reader and reader.is_alive() and reader is not threading.current_thread():
            reader.join(timeout=1.0)
        self._reader_thread = None

        for thread in getattr(self, "_stderr_threads", []):
            if thread.is_alive() and thread is not threading.current_thread():
                thread.join(timeout=0.2)
        self._stderr_threads = []

        port = getattr(self, "_forward_port", None)
        if port:
            try:
                self._adb_run(
                    ["forward", "--remove", f"tcp:{port}"],
                    timeout=5,
                    check=False,
                )
            except Exception:
                pass
        self._forward_port = None
        self._ffmpeg_process = None
        self._server_process = None

    def close(self) -> None:
        self._closed = True
        self._stop_pipeline()
