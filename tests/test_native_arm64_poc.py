import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import native_arm64_poc as arm64


class NativeArm64PocTests(unittest.TestCase):
    def test_target_package_is_verified_game(self):
        self.assertEqual(arm64.PACKAGE, "com.got.globalru")
        self.assertEqual(
            arm64.ACTIVITY,
            "com.unity3d.player.MyMainPlayerActivity",
        )

    def test_default_image_is_arm64_android_11(self):
        self.assertEqual(
            arm64.DEFAULT_SYSTEM_IMAGE,
            "system-images;android-30;google_apis;arm64-v8a",
        )

    def test_native_gate_accepts_arm64_without_bridge(self):
        self.assertTrue(arm64.is_native_arm64({
            "abi": "arm64-v8a",
            "abilist": "arm64-v8a,armeabi-v7a,armeabi",
            "native_bridge": "",
        }))

    def test_native_gate_rejects_x86_or_bridge(self):
        self.assertFalse(arm64.is_native_arm64({
            "abi": "arm64-v8a",
            "abilist": "x86_64,arm64-v8a",
            "native_bridge": "libhoudini.so",
        }))
        self.assertFalse(arm64.is_native_arm64({
            "abi": "x86_64",
            "abilist": "x86_64,arm64-v8a",
            "native_bridge": "libndk_translation.so",
        }))

    def test_choose_machine_prefers_ranchu(self):
        self.assertEqual(arm64.choose_machine(["virt", "ranchu"]), "ranchu")
        self.assertEqual(arm64.choose_machine(["virt"]), "virt")

    def test_inventory_finds_arm64_image_files(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            for name in ("kernel-ranchu", "ramdisk.img", "system.img", "userdata.img"):
                (root / name).write_bytes(b"x")
            inv = arm64.image_inventory(root)
        self.assertTrue(inv["kernel"].endswith("kernel-ranchu"))
        self.assertTrue(inv["system"].endswith("system.img"))

    def test_command_uses_aarch64_tcg_not_native_bridge(self):
        inv = {
            "kernel": r"C:\image\kernel-ranchu",
            "ramdisk": r"C:\image\ramdisk.img",
            "system": r"C:\image\system.img",
            "userdata": r"C:\image\userdata.img",
        }
        paths = {
            "userdata": Path(r"C:\runtime\userdata-qemu.img"),
            "hw": Path(r"C:\runtime\hardware-qemu.ini"),
        }
        with patch("native_arm64_poc.prepare_runtime", return_value=(inv, paths)), \
             patch("native_arm64_poc.choose_machine", return_value="ranchu"):
            cmd = arm64.build_direct_qemu_command()
        joined = " ".join(cmd).lower()
        self.assertIn("qemu-system-aarch64", joined)
        self.assertIn("tcg,thread=multi", joined)
        self.assertIn("type=ranchu", joined)
        self.assertNotIn("houdini", joined)
        self.assertNotIn("ndk_translation", joined)


if __name__ == "__main__":
    unittest.main()
