"""Continuous framebuffer transports for TUGARIN BOTS.

The GUI prefers a low-latency raw H.264 Android screenrecord stream decoded by
FFmpeg. If FFmpeg or screenrecord is unavailable, preview falls back to the
proven ADB PNG screencap path. Automation remains independent from this GUI
transport and continues to use DeviceBackend frames.
"""

from __future__ import annotations

import os
import queue
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np

from device_backend import BackendCapture, BackendError


WINDOWS_NO_WINDOW = (
    getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
)


@dataclass(frozen=True)
class StreamMetrics:
    transport: str
    fps: float
    latency_ms: float
    frames: int
    errors: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "transport": self.transport,
            "fps": self.fps,
            "latency_ms": self.latency_ms,
            "frames": self.frames,
            "errors": self.errors,
        }


class H264ScreenrecordCapture:
    """Decode Android's continuous H.264 screenrecord stream through FFmpeg.

    Android screenrecord supports raw H.264 output to stdout. The encoder has a
    finite recording window on many Android builds, so a short/ended stream is
    transparently restarted on the next frame request.
    """

    transport_name = "h264-screenrecord"

    def __init__(
        self,
        backend,
        *,
        ffmpeg_path: str | None = None,
        bit_rate: int = 4_000_000,
        max_width: int = 1280,
        max_height: int = 720,
        input_fps: int = 60,
        frame_timeout: float = 2.5,
    ):
        self.backend = backend
        self.ffmpeg_path = (
            ffmpeg_path
            or os.environ.get("TUGARIN_FFMPEG")
            or shutil.which("ffmpeg.exe")
            or shutil.which("ffmpeg")
        )
        if not self.ffmpeg_path:
            raise BackendError("FFmpeg is unavailable; use ADB screencap fallback.")

        adb_path = getattr(backend, "adb_path", None)
        if not adb_path:
            raise BackendError("H.264 preview requires an ADB-backed DeviceBackend.")
        self.adb_path = str(adb_path)
        self.bit_rate = max(500_000, int(bit_rate))
        self.input_fps = max(1, int(input_fps))
        self.frame_timeout = max(0.25, float(frame_timeout))
        self.max_width = max(320, int(max_width))
        self.max_height = max(240, int(max_height))

        health = backend.health()
        if not health.ready:
            raise BackendError("Android is not ready for H.264 preview.")
        match = re.search(r"(\d+)\s*x\s*(\d+)", health.resolution or "")
        if not match:
            raise BackendError(
                f"Could not determine Android framebuffer size: {health.resolution!r}"
            )
        self.source_width = int(match.group(1))
        self.source_height = int(match.group(2))
        if self.source_width <= 0 or self.source_height <= 0:
            raise BackendError("Android framebuffer dimensions are invalid.")

        self.width, self.height = self._fit_preview_size(
            self.source_width,
            self.source_height,
            self.max_width,
            self.max_height,
        )

        self._adb_process = None
        self._ffmpeg_process = None
        self._reader_thread = None
        self._reader_stop = threading.Event()
        self._frame_queue = queue.Queue(maxsize=2)
        self._reader_error = None
        self._closed = False
        self._start_pipeline()

    @staticmethod
    def _fit_preview_size(
        width: int,
        height: int,
        max_width: int = 1280,
        max_height: int = 720,
    ) -> tuple[int, int]:
        width = max(2, int(width))
        height = max(2, int(height))
        scale = min(1.0, max_width / width, max_height / height)
        out_w = max(2, int(width * scale))
        out_h = max(2, int(height * scale))
        # MediaCodec/FFmpeg are happier with even dimensions.
        out_w -= out_w % 2
        out_h -= out_h % 2
        return max(2, out_w), max(2, out_h)

    def _start_pipeline(self) -> None:
        self._stop_pipeline()
        if self._closed:
            raise BackendError("H.264 preview is closed.")

        adb_cmd = [
            self.adb_path,
            "-s", self.backend.serial,
            "exec-out",
            "screenrecord",
            "--output-format=h264",
            "--bit-rate", str(self.bit_rate),
            "--size", f"{self.width}x{self.height}",
            "-",
        ]
        self._adb_process = subprocess.Popen(
            adb_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            creationflags=WINDOWS_NO_WINDOW,
            bufsize=0,
        )
        if self._adb_process.stdout is None:
            self._stop_pipeline()
            raise BackendError("ADB screenrecord did not expose stdout.")

        ffmpeg_cmd = [
            str(self.ffmpeg_path),
            "-hide_banner",
            "-loglevel", "error",
            "-fflags", "nobuffer+discardcorrupt",
            "-flags", "low_delay",
            "-framerate", str(self.input_fps),
            "-use_wallclock_as_timestamps", "1",
            "-probesize", "32",
            "-analyzeduration", "0",
            "-f", "h264",
            "-i", "pipe:0",
            "-an",
            "-vf", "setpts=0",
            "-fps_mode", "passthrough",
            "-f", "rawvideo",
            "-pix_fmt", "bgr24",
            "pipe:1",
        ]
        self._ffmpeg_process = subprocess.Popen(
            ffmpeg_cmd,
            stdin=self._adb_process.stdout,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            creationflags=WINDOWS_NO_WINDOW,
            bufsize=0,
        )
        # FFmpeg now owns the read end; the parent must not retain a duplicate.
        self._adb_process.stdout.close()
        if self._ffmpeg_process.stdout is None:
            self._stop_pipeline()
            raise BackendError("FFmpeg did not expose decoded-video stdout.")

        self._reader_stop.clear()
        self._reader_error = None
        self._frame_queue = queue.Queue(maxsize=2)
        self._reader_thread = threading.Thread(
            target=self._reader_loop,
            name="tugarin-h264-reader",
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
            self._reader_error = "H.264 decoder pipeline is not running."
            return

        frame_bytes = self.width * self.height * 3
        try:
            while not self._reader_stop.is_set():
                raw = self._read_exact(process.stdout, frame_bytes)
                if len(raw) != frame_bytes:
                    self._reader_error = (
                        "H.264 decoder stream ended mid-frame: "
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
            self._reader_error = f"H.264 reader failed: {exc}"

    def _read_frame(self) -> np.ndarray:
        if self._ffmpeg_process is None:
            raise BackendError("H.264 decoder pipeline is not running.")
        try:
            return self._frame_queue.get(timeout=self.frame_timeout)
        except queue.Empty as exc:
            detail = self._reader_error or (
                f"no decoded frame within {self.frame_timeout:.2f}s"
            )
            raise BackendError(f"H.264 preview stalled: {detail}") from exc

    def grab(self):
        try:
            frame = self._read_frame()
        except BackendError:
            # screenrecord commonly has a finite recording cap. Restart once;
            # if the runtime/codec is genuinely broken, FallbackCapture will
            # demote the transport to PNG screencap.
            self._start_pipeline()
            frame = self._read_frame()
        return (
            frame,
            f"h264:{self.backend.serial}",
            {
                "left": 0,
                "top": 0,
                "width": self.source_width,
                "height": self.source_height,
            },
        )

    def _stop_pipeline(self) -> None:
        self._reader_stop.set()
        for process in (self._ffmpeg_process, self._adb_process):
            if process is None:
                continue
            try:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=1.0)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=1.0)
            except (OSError, subprocess.SubprocessError):
                pass
        reader = self._reader_thread
        if reader and reader.is_alive() and reader is not threading.current_thread():
            reader.join(timeout=1.0)
        self._reader_thread = None
        self._ffmpeg_process = None
        self._adb_process = None

    def close(self) -> None:
        self._closed = True
        self._stop_pipeline()


class FallbackCapture:
    """Use a preferred capture until it fails, then permanently demote."""

    def __init__(self, preferred, fallback):
        self.preferred = preferred
        self.fallback = fallback
        self.active = preferred

    @property
    def transport_name(self) -> str:
        return getattr(
            self.active,
            "transport_name",
            "adb-screencap" if isinstance(self.active, BackendCapture) else "preview",
        )

    def grab(self):
        try:
            return self.active.grab()
        except Exception:
            if self.active is self.fallback:
                raise
            try:
                self.preferred.close()
            except Exception:
                pass
            self.active = self.fallback
            return self.active.grab()

    def close(self) -> None:
        for capture in (self.preferred, self.fallback):
            try:
                capture.close()
            except Exception:
                pass


class AutoPreviewCapture:
    """Lazily choose H.264 inside the worker so GUI construction never blocks."""

    def __init__(self, backend):
        self.backend = backend
        self.active = None
        self._closed = False

    @property
    def transport_name(self) -> str:
        if self.active is None:
            return "preview-starting"
        return getattr(self.active, "transport_name", "adb-screencap")

    def _ensure_active(self):
        if self._closed:
            raise BackendError("Preview capture is closed.")
        if self.active is not None:
            return
        fallback = BackendCapture(self.backend)
        try:
            preferred = H264ScreenrecordCapture(self.backend)
            self.active = FallbackCapture(preferred, fallback)
        except Exception:
            self.active = fallback

    def grab(self):
        self._ensure_active()
        return self.active.grab()

    def close(self) -> None:
        self._closed = True
        if self.active is not None:
            try:
                self.active.close()
            finally:
                self.active = None


def create_preview_capture(backend):
    """Return a non-blocking lazy preview selector."""

    return AutoPreviewCapture(backend)


class ContinuousFrameStream:
    def __init__(
        self,
        capture,
        *,
        on_frame: Callable[[object, str, dict, StreamMetrics], None],
        on_error: Callable[[str], None],
        target_fps: float = 30.0,
        transport: str = "preview",
    ):
        self.capture = capture
        self.on_frame = on_frame
        self.on_error = on_error
        self.target_fps = max(0.5, float(target_fps))
        self.transport = str(transport)
        self._stop = threading.Event()
        self._thread = None
        self._frames = 0
        self._errors = 0
        self._started_at = 0.0

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._started_at = time.monotonic()
        self._thread = threading.Thread(
            target=self._run,
            name="tugarin-bots-frame-stream",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        # Close first so a decoder blocked in read() is released before join.
        try:
            self.capture.close()
        except Exception:
            pass
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=max(0.0, float(timeout)))
        self._thread = None

    def _metrics(self, latency_ms: float) -> StreamMetrics:
        elapsed = max(0.001, time.monotonic() - self._started_at)
        transport = getattr(self.capture, "transport_name", self.transport)
        return StreamMetrics(
            transport=str(transport),
            fps=self._frames / elapsed,
            latency_ms=float(latency_ms),
            frames=self._frames,
            errors=self._errors,
        )

    def _run(self) -> None:
        frame_interval = 1.0 / self.target_fps
        backoff = 0.25
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                frame, title, rect = self.capture.grab()
                latency_ms = (time.monotonic() - started) * 1000.0
                self._frames += 1
                backoff = 0.25
                self.on_frame(frame, title, rect, self._metrics(latency_ms))
            except Exception as exc:
                self._errors += 1
                self.on_error(str(exc))
                if self._stop.wait(backoff):
                    break
                backoff = min(2.0, backoff * 1.5)
                continue

            delay = frame_interval - (time.monotonic() - started)
            if delay > 0 and self._stop.wait(delay):
                break
