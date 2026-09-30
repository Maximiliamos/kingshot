import threading
import time
import unittest

import numpy as np

from frame_stream import AutoPreviewCapture, ContinuousFrameStream, FallbackCapture


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
