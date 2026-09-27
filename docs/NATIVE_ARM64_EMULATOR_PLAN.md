# WAR BOT native ARM64 emulator PoC

## Why this exists

Both Windows x86 Android paths have now failed inside CPU translation layers:

- official x86_64 Android Emulator: the game's ARM64 package fails in the
  ARM translation/native-bridge path;
- BlueStacks Android 11 Rvc64: the game reaches the loading flow but crashes
  reproducibly in `/system/lib64/libhoudini.so` with SIGSEGV.

The next experiment removes that class of failure entirely. The guest CPU and
Android userspace are ARM64. On an x86-64 Windows host, ARM64 instructions are
executed by QEMU TCG rather than by an Android ARM-to-x86 native bridge.

This is slower than WHPX/BlueStacks. The first objective is correctness and
stability. Performance tuning comes only after the game boots reliably.

## Non-goals

The PoC does **not**:

- patch `com.got.globalru`;
- replace or edit game native libraries;
- hide that Android is virtualized;
- bypass Play Integrity or server restrictions;
- modify anti-cheat / emulator-detection code.

## Runtime

Default system image:

`system-images;android-30;google_apis;arm64-v8a`

Current raw-engine probe:

`C:\Program Files\qemu\qemu-system-aarch64.exe` (QEMU 11.1, TCG)

Default runtime data:

`C:\warbot_arm64_runtime`

The game remains the verified package:

- package: `com.got.globalru`
- activity: `com.unity3d.player.MyMainPlayerActivity`
- APKS: 1.12.10 / 163 / arm64-v8a

## Gates

### A0 — runtime probe

Required:

- `adb.exe`
- `sdkmanager.bat`
- `qemu-system-aarch64.exe`
- ARM64 Android system image
- QEMU machine `ranchu` or `virt`

### A1 — native ARM64 boot

The guest must reach `sys.boot_completed=1`.

### A2 — no translation bridge

Required properties:

```text
ro.product.cpu.abi = arm64-v8a
ro.product.cpu.abilist = arm64...
ro.dalvik.vm.native.bridge = <empty/0/none>
```

Any x86 ABI or Houdini/libndk_translation fails this gate.

### A3 — game install

Install the three known Google Play splits with `adb install-multiple`.

### A4 — game run

The game must remain alive after launch. Any crash is collected from the native
crash log before the next change.

### A5 — framebuffer and input

Only after A4:

- `adb exec-out screencap -p`
- ADB tap/swipe
- tap-to-frame latency benchmark
- software GPU / renderer work if Unity needs more graphics capability

### A6 — WAR BOT integration

Only after A1-A5 pass, implement `NativeArm64Backend` behind the existing
DeviceBackend contract. Tutorial/OCR/state-machine code must not be rewritten.

## Commands

```powershell
git checkout feature/native-arm64-emulator
git pull

python .\native_arm64_poc.py probe
python .\native_arm64_poc.py install-image
python .\native_arm64_poc.py prepare --wipe
python .\native_arm64_poc.py command
python .\native_arm64_poc.py start --wipe
python .\native_arm64_poc.py status
python .\native_arm64_poc.py verify-native
```

Do not run `install-game` until `verify-native` passes.

Then:

```powershell
python .\native_arm64_poc.py install-game
python .\native_arm64_poc.py verify-game --wait-seconds 60
python .\native_arm64_poc.py capture
```

## Expected first iteration

The first test is deliberately CPU/boot focused and starts with the simplest
software framebuffer. If Android boots but Unity lacks a usable GLES renderer,
that becomes the next isolated task. We should not mix CPU correctness, boot,
ADB and GPU debugging in one change.

## Evidence update — 2026-09-27

`-debug-init` exposed the Android Emulator launcher's final ranchu topology.
The launcher did not enter Android boot: its generated command includes
`-soundhw hda`, but ranchu has no PCI bus. The headless binary reports the
exact terminal error `PCI bus not available for hda`. `-no-audio` does not
prevent that generated option, and QEMU passthrough appends rather than
replaces it.

Upstream QEMU 11.1 was then tested without the launcher layer. The stock
Android 11 ARM64 kernel boots on `virt` under TCG, mounts system/vendor/data,
finishes file-based encryption, starts zygote and adbd, and contains no x86
native bridge. A fresh runtime encryption-key qcow2 overlay is required for a
clean userdata image.

This is not A1/A2 PASS yet. The stock vendor contains only ranchu graphics
HALs (`hwcomposer.ranchu.so` and ranchu mapper/Vulkan modules). Upstream QEMU
does not provide Android's goldfish pipe, so hwcomposer aborts and restarts
SurfaceFlinger/zygote; ADB remains offline and `sys.boot_completed` is not 1.

Next step: obtain/build a Google/AOSP QEMU ranchu runner whose final topology
omits the invalid HDA device, or make the launcher generate a supported audio
device. Do not add generic virtio-gpu flags to the upstream path: the installed
vendor image has no matching DRM/virtio hwcomposer implementation.

The packaged ARM64 core also exposes a direct positional-QEMU path through
`-fuchsia`. The PoC now uses that supported entry point to start Google
`ranchu` and gfxstream without the launcher-generated HDA device; it does not
patch the emulator binary, Android image, or game. The kernel sees the five
block devices and the GPT `super` partition, but first-stage init currently
stops at `partition(s) not found: system`. HDA is therefore resolved, while
A1/A2 remain FAIL until the launcher-equivalent dynamic-partition mapping is
reproduced and ADB reaches `device` with `sys.boot_completed=1`.


### Evidence update — partition ordering fix

AOSP's ARM64 emulator target intentionally emits block images in the order
`vendor -> encryption -> userdata -> cache -> system`. On ARM/ranchu the
virtio-MMIO transport assignment is effectively reversed: command-line
`-device` entries are attached to decreasing MMIO addresses. With five block
devices this puts the fifth device (system/super) at
`a003600.virtio_mmio`, which matches the image's verified-boot
`androidboot.boot_devices` value.

The direct `-fuchsia` command had accidentally used the opposite device
order (`system ... vendor`). That made `a003600.virtio_mmio` point at the
vendor disk while first-stage init searched there for the dynamic-partition
backing device and failed with `partition(s) not found: system`.

The PoC now mirrors the launcher order and drive indices exactly:
`vendor(0), encrypt(1), userdata(2), cache(3), system(4)`, while retaining
`androidboot.boot_devices=a003600.virtio_mmio`. Unit tests pin both the
ordering and the boot-device value.

Next real-host gate: rerun `start --wipe`. PASS requires ADB `device`,
`sys.boot_completed=1`, ARM64 ABI, no native bridge, and a valid screenshot.
