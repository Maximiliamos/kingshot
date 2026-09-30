import unittest
from pathlib import Path

from scrcpy_transport import (
    SCRCPY_SERVER_SHA256,
    SCRCPY_VERSION,
    ScrcpyServerCapture,
)


ROOT = Path(__file__).resolve().parents[1]
PROVISION = (ROOT / "scripts" / "provision_scrcpy_server.ps1").read_text(
    encoding="utf-8-sig"
)
SOURCE = (ROOT / "scrcpy_transport.py").read_text(encoding="utf-8")


class ScrcpyTransportTests(unittest.TestCase):
    def test_default_profile_is_bounded_for_wsa_gameplay(self):
        source = SOURCE
        self.assertIn("max_size: int = 960", source)
        self.assertIn("bit_rate: int = 2_000_000", source)
        self.assertIn("max_fps: int = 15", source)
        self.assertIn("queue.Queue(maxsize=1)", source)

    def test_pinned_official_server_identity(self):
        self.assertEqual(SCRCPY_VERSION, "4.1")
        self.assertEqual(
            SCRCPY_SERVER_SHA256,
            "deacb991ed2509715160ffdc7907e47b4160eb30d1566217e9047fd5b8850cae",
        )
        self.assertIn(
            "https://github.com/Genymobile/scrcpy/releases/download/v4.1/"
            "scrcpy-server-v4.1",
            PROVISION,
        )
        self.assertIn(SCRCPY_SERVER_SHA256, PROVISION)

    def test_fit_size_caps_1080p_at_720p(self):
        self.assertEqual(
            ScrcpyServerCapture._fit_size(1920, 1080, 1280),
            (1280, 720),
        )

    def test_server_runs_inside_android_over_adb_forward(self):
        self.assertIn("localabstract:", SOURCE)
        self.assertIn("socket_name", SOURCE)
        self.assertIn("scrcpy_", SOURCE)
        self.assertIn("app_process / com.genymobile.scrcpy.Server", SOURCE)
        self.assertIn("tunnel_forward=true", SOURCE)
        self.assertIn("raw_stream=true", SOURCE)
        self.assertIn("audio=false control=false", SOURCE)
        self.assertIn("tcp://127.0.0.1:", SOURCE)

    def test_transport_verifies_hash_before_push(self):
        hash_index = SOURCE.index("hashlib.sha256")
        push_index = SOURCE.index('"push"')
        self.assertLess(hash_index, push_index)
        self.assertIn("SHA-256 mismatch", SOURCE)

    def test_transport_removes_adb_forward_on_close(self):
        self.assertIn('"forward", "--remove"', SOURCE)


if __name__ == "__main__":
    unittest.main()
