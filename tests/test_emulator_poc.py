import unittest
from unittest.mock import patch

import emulator_poc


class EmulatorPocTests(unittest.TestCase):
    def test_target_is_verified_game_package(self):
        self.assertEqual(emulator_poc.PACKAGE, "com.got.globalru")
        self.assertEqual(emulator_poc.ACTIVITY, "com.unity3d.player.MyMainPlayerActivity")

    def test_install_uses_all_required_splits(self):
        with patch("emulator_poc.validate_files"), patch("emulator_poc.adb") as adb:
            adb.return_value.stdout = "Success\n"
            emulator_poc.install_apks()
        args = adb.call_args.args
        self.assertEqual(args[:2], ("install-multiple", "-r"))
        self.assertTrue(str(args[2]).endswith("base.apk"))
        self.assertTrue(str(args[3]).endswith("split_config.arm64_v8a.apk"))
        self.assertTrue(str(args[4]).endswith("split_game_asset.apk"))

    def test_avd_uses_x86_google_play_image_for_windows_host(self):
        self.assertEqual(
            emulator_poc.SYSTEM_IMAGE,
            "system-images;android-34;google_apis_playstore;x86_64",
        )

    def test_verify_game_fails_closed_when_process_exits(self):
        responses = [type("Result", (), {"stdout": ""})(), type("Result", (), {"stdout": "crash"})()]
        with patch("emulator_poc.launch_game"), patch("emulator_poc.time.sleep"), \
                patch("emulator_poc.adb", side_effect=responses), \
                patch("pathlib.Path.write_text"):
            with self.assertRaisesRegex(RuntimeError, "G2 FAIL"):
                emulator_poc.verify_game(0)


if __name__ == "__main__":
    unittest.main()
