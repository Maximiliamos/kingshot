import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bluestacks_poc


class BlueStacksPocTests(unittest.TestCase):
    def test_target_matches_verified_game(self):
        self.assertEqual(bluestacks_poc.PACKAGE, "com.got.globalru")
        self.assertEqual(
            bluestacks_poc.ACTIVITY,
            "com.unity3d.player.MyMainPlayerActivity",
        )

    def test_parses_instances_and_adb_port(self):
        content = """\
bst.instance.Rvc64.display_name="WAR BOT Android 11"
bst.instance.Rvc64.abi_list="x86,x64,arm,arm64"
bst.instance.Rvc64.status.adb_port="5565"
bst.instance.Pie64.display_name="Legacy"
bst.instance.Pie64.adb_port="5575"
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bluestacks.conf"
            path.write_text(content, encoding="utf-8")
            values = bluestacks_poc.parse_config(path)
            instances = bluestacks_poc.list_instances(values)

        by_name = {item["name"]: item for item in instances}
        self.assertEqual(by_name["Rvc64"]["adb_port"], "5565")
        self.assertIn("arm64", by_name["Rvc64"]["abi_list"])
        self.assertEqual(by_name["Pie64"]["adb_port"], "5575")

    def test_prefers_android_11_instance(self):
        instances = [
            {"name": "Pie64", "display_name": "Pie", "abi_list": "x64,arm64", "android_image": "", "adb_port": "5555"},
            {"name": "Rvc64", "display_name": "WAR BOT", "abi_list": "x64,arm64", "android_image": "", "adb_port": "5565"},
        ]
        with patch("bluestacks_poc.list_instances", return_value=instances):
            self.assertEqual(bluestacks_poc.choose_instance()["name"], "Rvc64")

    def test_verify_fails_closed_when_game_exits(self):
        instance = {"name": "Rvc64", "display_name": "WAR BOT", "abi_list": "arm64", "android_image": "", "adb_port": "5565"}
        responses = [type("Result", (), {"stdout": ""})()]
        with patch("bluestacks_poc.launch_game"), patch("bluestacks_poc.time.sleep"),              patch("bluestacks_poc.adb", side_effect=responses),              patch("bluestacks_poc.collect_crash_evidence", return_value=Path("evidence.txt")):
            with self.assertRaisesRegex(RuntimeError, "G2 FAIL"):
                bluestacks_poc.verify_game(instance, 0)

    def test_launch_requires_play_store_install(self):
        instance = {"name": "Rvc64", "display_name": "WAR BOT", "abi_list": "arm64", "android_image": "", "adb_port": "5565"}
        with patch("bluestacks_poc.package_installed", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "Google Play"):
                bluestacks_poc.launch_game(instance)


if __name__ == "__main__":
    unittest.main()
