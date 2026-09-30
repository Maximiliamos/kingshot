"""Continuous framebuffer transport for the GUI.

This removes Qt-timer-driven one-thread-per-frame polling. The current stable
transport is ADB screencap through BackendCapture; the stream abstraction keeps
video transport independent so a lower-latency decoder can replace it without
changing the GUI or automation layers.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Any


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


class ContinuousFrameStream:
    def __init__(
        self,
        capture,
        *,
        on_frame: Callable[[object, str, dict, StreamMetrics], None],
        on_error: Callable[[str], None],
        target_fps: float = 4.0,
        transport: str = "adb-screencap",
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
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=max(0.0, float(timeout)))
        try:
            self.capture.close()
        except Exception:
            pass
        self._thread = None

    def _metrics(self, latency_ms: float) -> StreamMetrics:
        elapsed = max(0.001, time.monotonic() - self._started_at)
        return StreamMetrics(
            transport=self.transport,
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
