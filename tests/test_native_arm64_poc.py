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



    def test_qemu_environment_prepends_emulator_library_dirs(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            qemu = root / "emulator" / "qemu" / "windows-x86_64"
            lib64 = root / "emulator" / "lib64"
            qemu.mkdir(parents=True)
            lib64.mkdir(parents=True)
            with patch("native_arm64_poc.SDK_ROOT", root), \
                 patch("native_arm64_poc.QEMU_DIR", qemu), \
                 patch.dict("os.environ", {"PATH": r"C:\Windows\System32"}, clear=False):
                env = arm64.qemu_environment()
        self.assertIn(str(qemu), env["PATH"])
        self.assertIn(str(lib64), env["PATH"])
        self.assertTrue(env["PATH"].endswith(r"C:\Windows\System32"))

    def test_qemu_machine_probe_parses_stderr(self):
        result = type("Result", (), {
            "stdout": "",
            "stderr": "Supported machines are:\\nranchu Android Emulator\\nvirt ARM Virtual Machine\\n",
            "returncode": 0,
        })()
        with patch("native_arm64_poc.QEMU_ARM64") as qemu, \
             patch("native_arm64_poc.run", return_value=result):
            qemu.is_file.return_value = True
            probe = arm64.qemu_machine_probe()
        self.assertEqual(probe["returncode"], 0)
        self.assertEqual(probe["names"][:2], ["ranchu", "virt"])


    def test_qemu_probe_enters_passthrough_mode(self):
        result = type("Result", (), {
            "stdout": "Supported machines are:\\nvirt ARM Virtual Machine\\n",
            "stderr": "",
            "returncode": 0,
        })()
        with patch("native_arm64_poc.QEMU_ARM64") as qemu, \
             patch("native_arm64_poc.run", return_value=result) as run:
            qemu.is_file.return_value = True
            arm64.qemu_machine_probe()
        args = run.call_args.args[0]
        self.assertEqual(args[1:4], ["-qemu", "-machine", "help"])

    def test_choose_machine_prefers_ranchu(self):
        self.assertEqual(arm64.choose_machine(["virt", "ranchu"]), "ranchu")
        self.assertEqual(arm64.choose_machine(["virt"]), "virt")
        self.assertEqual(arm64.choose_machine(["virt-8.2", "virt-8.1"]), "virt-8.2")
        self.assertEqual(arm64.choose_machine(["foo"], strict=False), "")

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
