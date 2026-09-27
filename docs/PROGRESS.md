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

Latest real-host result and runtime architecture:

- legacy Google `ranchu` mounted all Android partitions and eventually
  exposed adbd, but `sys.boot_completed` stayed empty;
- zygote plus multiple unrelated HAL/media processes repeatedly crashed;
- the kernel explicitly reported missing PSCI CPU-on support and activated
  only CPU0 despite a 4-vCPU configuration;
- `qemu.media.ccodec=0` was present on the real kernel command line, so the
  remaining failure is broader than a missing Codec2 flag;
- production now uses Google's Android-modified ARM `virt` machine, which
  AOSP specifically extended with goldfish/ranchu devices while retaining
  the maintained virt PSCI/multicore topology;
- direct mode now initializes the qemu boot-property channel explicitly;
- machine-specific derived DTBs prevent ranchu/virt topology mixing;
- switching machine automatically recreates only disposable emulator runtime,
  never the PC-side WAR BOT nickname counter/state;
- boot diagnostics also attempt to collect Android crash-buffer/tombstones
  once adbd is reachable.

The remaining runtime gate is the first real Windows-host `virt` boot proving
ADB=`device` and `sys.boot_completed=1`.

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
| Tutorial/state machine | CODE COMPLETE / REAL FLOW PENDING | validate actual native-emulator screens |
| Exact State #3 | DOUBLE VISUAL GATE / REAL VERIFY PENDING | row + explicit confirm template |
| Rename/counter loop | CODE COMPLETE / REAL VERIFY PENDING | exact `Тугарин<N>` with no space |
| App-data reset/repeat | IMPLEMENTED | PC counter preserved; server limits stop |
| GUI integration | IMPLEMENTED | Start auto-bootstraps Android + game |

## Safety

The project does not:

- patch the game APK or native libraries;
- hide emulator properties;
- spoof Play Integrity/attestation;
- use Magisk/root/Frida to evade restrictions;
- bypass server/account character limits.

If the game/server rejects the environment or account flow, the bot must stop
and surface the condition instead of bypassing it.
