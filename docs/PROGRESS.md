# WAR BOT progress

Updated: 2026-09-27

## Application layer

The application is no longer tied to a physical HONOR phone or a scrcpy
desktop window.

Implemented on `feature/unified-android-backend`:

- `DeviceBackend` contract for Android transport;
- `NativeArm64Backend` for the native ARM64 QEMU runtime;
- generic `AdbDeviceBackend` for an already-running Android target;
- device-scoped ADB serial for every command;
- Android health/readiness checks;
- strict native ARM64 gate before game install/launch;
- direct screenshot through `adb exec-out screencap -p`;
- tap, swipe/hold, keyevent, shell and app lifecycle through one backend;
- optional uiautomator2 path for Android system UI;
- existing OpenCV/Unity vision/state machine kept intact;
- GUI backend selection and ARM64 runtime start/stop controls;
- backend-aware live preview;
- agent-friendly `warbot_cli.py`;
- complete installer copies the application instead of only `bot.py`;
- CI discovers and runs the whole test suite.

Legacy scrcpy is retained only as a diagnostic capture mode. It is not the
production target.

## Native ARM64 runtime

Runtime work remains on `feature/native-arm64-emulator` / Draft PR #5.

Confirmed progress:

- ARM64 Android 11 image is installed;
- Google ranchu core runs without the launcher-added invalid HDA device;
- dynamic partitions are described through a derived ranchu DTB;
- `super` is discovered;
- `system`, `vendor`, `product` and `system_ext` mount;
- userdata/FBE initializes;
- boot reaches zygote/SurfaceFlinger;
- no game APK has been installed before the native ARM64 gate.

Current runtime blocker:

- `app_process64` repeatedly crashes with SIGSEGV while executing inside
  `libcodec2_vndk.so`;
- ADB therefore does not yet reach stable `device`;
- `sys.boot_completed` is not yet `1`;
- A1/A2 remain FAIL.

The runtime diagnosis should continue independently: collect reproducible
zygote crashes, symbolize the exact PC/offset and perform the planned
Google-ranchu vs upstream-QEMU comparison before changing more CPU models or
guest libraries.

## What becomes immediately usable after A1/A2

Once the ARM64 guest reaches:

```text
ADB = device
sys.boot_completed = 1
ro.product.cpu.abi = arm64-v8a
no x86 in abilist
ro.dalvik.vm.native.bridge = empty / 0 / none
```

the application layer can immediately:

1. capture a frame;
2. send tap/swipe/key input;
3. install the three verified game splits;
4. launch/stop the game;
5. reuse existing OpenCV templates and action gate;
6. continue the character/tutorial/rename state machine;
7. surface the same runtime in the GUI and CLI.

No second transport rewrite should be necessary.

## Remaining MVP gates

| Gate | State | Required evidence |
|---|---|---|
| Stable ARM64 Android boot | BLOCKED | ADB device + boot_completed=1 |
| Native ABI gate | BLOCKED by boot | arm64-v8a, no x86, no bridge |
| Frame capture | CODE READY | real PNG from native guest |
| Input | CODE READY | tap/swipe changes real guest frame |
| Game install | CODE READY, GATED | install-multiple after ARM64 gate |
| Game launch | CODE READY, GATED | Unity stays alive |
| Tutorial/state machine | PARTIAL | complete real native-emulator flow |
| Exact State #3 | NEEDS REAL VERIFICATION | never select #23 |
| Rename/counter loop | PARTIAL | verified repeated cycle |
| App-data reset/repeat | SAFE API READY | integrate after cycle validation |
| GUI integration | IMPLEMENTED | validate against booted guest |

## Safety

The project does not:

- patch the game APK or native libraries;
- hide emulator properties;
- spoof Play Integrity/attestation;
- use Magisk/root/Frida to evade restrictions;
- bypass server/account character limits.

If the game/server rejects the environment or account flow, the bot must stop
and surface the condition instead of bypassing it.
