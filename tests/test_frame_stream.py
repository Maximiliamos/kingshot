import queue
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

import numpy as np

from device_backend import BackendError
from frame_stream import (
    AutoPreviewCapture,
    ContinuousFrameStream,
    FallbackCapture,
    H264ScreenrecordCapture,
)


class FakeCapture:
    def __init__(self):
        self.calls = 0
        self.closed = False

    def grab(self):
        self.calls += 1
        frame = np.zeros((20, 10, 3), dtype=np.uint8)
        return frame, "fake", {"left": 0, "top": 0, "width": 10, "height": 20}

    def close(self):
        self.closed = True


class FailingCapture(FakeCapture):
    def grab(self):
        self.calls += 1
        raise RuntimeError("capture down")


class FailingOnceCapture(FakeCapture):
    transport_name = "preferred"

    def grab(self):
        self.calls += 1
        raise RuntimeError("preferred failed")


class NamedFallbackCapture(FakeCapture):
    transport_name = "fallback"


class FrameStreamTests(unittest.TestCase):
    def test_h264_preview_downscales_1080p_to_720p(self):
        self.assertEqual(
            H264ScreenrecordCapture._fit_preview_size(1920, 1080, 1280, 720),
            (1280, 720),
        )

    def test_h264_preview_keeps_smaller_framebuffer_native(self):
        self.assertEqual(
            H264ScreenrecordCapture._fit_preview_size(960, 540, 1280, 720),
            (960, 540),
        )

    def test_h264_preview_size_is_even_and_preserves_aspect(self):
        width, height = H264ScreenrecordCapture._fit_preview_size(
            2376, 1060, 1280, 720
        )
        self.assertEqual(width % 2, 0)
        self.assertEqual(height % 2, 0)
        self.assertLessEqual(width, 1280)
        self.assertLessEqual(height, 720)
        self.assertAlmostEqual(width / height, 2376 / 1060, delta=0.02)

    def test_h264_frame_wait_is_bounded(self):
        capture = H264ScreenrecordCapture.__new__(H264ScreenrecordCapture)
        capture._ffmpeg_process = object()
        capture._frame_queue = queue.Queue()
        capture.frame_timeout = 0.01
        capture._reader_error = None
        with self.assertRaisesRegex(BackendError, "preview stalled"):
            capture._read_frame()

    def test_h264_preview_reports_physical_geometry_after_downscale(self):
        capture = H264ScreenrecordCapture.__new__(H264ScreenrecordCapture)
        capture.width = 1280
        capture.height = 720
        capture.source_width = 1920
        capture.source_height = 1080
        capture.backend = type("Backend", (), {"serial": "127.0.0.1:58526"})()
        capture._read_frame = lambda: np.zeros((720, 1280, 3), dtype=np.uint8)
        frame, _, rect = capture.grab()
        self.assertEqual(frame.shape, (720, 1280, 3))
        self.assertEqual(rect["width"], 1920)
        self.assertEqual(rect["height"], 1080)

    def test_fallback_capture_demotes_after_primary_failure(self):
        preferred = FailingOnceCapture()
        fallback = NamedFallbackCapture()
        capture = FallbackCapture(preferred, fallback)
        frame, title, rect = capture.grab()
        self.assertEqual(frame.shape, (20, 10, 3))
        self.assertEqual(capture.transport_name, "fallback")
        self.assertTrue(preferred.closed)

    def test_auto_preview_is_lazy_before_first_grab(self):
        backend = object()
        capture = AutoPreviewCapture(backend)
        self.assertIsNone(capture.active)
        self.assertEqual(capture.transport_name, "preview-starting")
        capture.close()

    def test_auto_preview_prefers_scrcpy_h264(self):
        backend = MagicMock()
        preferred = FakeCapture()
        preferred.transport_name = "scrcpy-h264"
        capture = AutoPreviewCapture(backend)
        with patch("frame_stream.ScrcpyServerCapture", return_value=preferred):
            capture._ensure_active()
        self.assertEqual(capture.transport_name, "scrcpy-h264")
        capture.close()
        self.assertTrue(preferred.closed)

    def test_continuous_stream_emits_frames_and_metrics(self):
        capture = FakeCapture()
        ready = threading.Event()
        seen = []

        def on_frame(frame, title, rect, metrics):
            seen.append((frame.shape, title, rect, metrics))
            if len(seen) >= 2:
                ready.set()

        stream = ContinuousFrameStream(
            capture,
            on_frame=on_frame,
            on_error=lambda error: self.fail(error),
            target_fps=30,
            transport="adb-screencap",
        )
        stream.start()
        self.assertTrue(ready.wait(2.0))
        stream.stop()

        self.assertGreaterEqual(capture.calls, 2)
        self.assertTrue(capture.closed)
        self.assertGreater(seen[-1][3].fps, 0)
        self.assertEqual(seen[-1][3].transport, "adb-screencap")

    def test_stream_errors_are_bounded_by_backoff_and_stop_cleanly(self):
        capture = FailingCapture()
        error_seen = threading.Event()
        errors = []

        stream = ContinuousFrameStream(
            capture,
            on_frame=lambda *args: None,
            on_error=lambda error: (errors.append(error), error_seen.set()),
            target_fps=30,
        )
        stream.start()
        self.assertTrue(error_seen.wait(1.0))
        time.sleep(0.1)
        stream.stop()

        self.assertTrue(errors)
        self.assertTrue(capture.closed)
        self.assertFalse(stream.running)


if __name__ == "__main__":
    unittest.main()
