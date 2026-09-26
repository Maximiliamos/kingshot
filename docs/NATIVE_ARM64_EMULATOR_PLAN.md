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

Default direct engine:

`C:\Android\Sdk\emulator\qemu\windows-x86_64\qemu-system-aarch64.exe`

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
