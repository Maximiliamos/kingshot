# Emulator PoC results — 2026-09-27

## Inputs

- Game: «Война за трон», `com.got.globalru`.
- Publisher: Echofun Interactive Limited.
- APKS: version `1.12.10`, versionCode `163`.
- Launcher: `com.unity3d.player.MyMainPlayerActivity`.
- APK set: `base.apk`, `split_config.arm64_v8a.apk`,
  `split_game_asset.apk`.

SHA-256:

- base: `C67E3B90F9261B1EDD0A000E0AB29FE58EECD77F90117407ED0CD944A5FD816D`
- arm64 split: `3C6A75C33830E054ABC92F88C7A8F770E9A9B56E5C064F555909779016344264`
- asset split: `88005307787890E452CAED4160A63B54DD5A1DBE22E36D5B01E4E7C6CAD60718`

## Environment

- OpenJDK 17.0.20.1.
- Android Emulator 37.1.11.
- Platform Tools 37.0.1.
- Windows Hypervisor Platform: installed and usable.
- Android 14 / API 34 Google Play system images.

## Gate results

### Native ARM64 AVD

FAIL before boot. QEMU2 on the x86-64 Windows host rejects an ARM64 AVD because
the AVD and host architectures do not match.

### G1 — install

PASS on the x86_64 Google Play AVD. It exposes `x86_64,arm64-v8a` through the
official `libndk_translation.so`; `adb install-multiple` installed all splits.

### G2 — game run/render

FAIL. The launcher activity starts, then `com.got.globalru` exits. Android
Dropbox records:

```text
java.lang.UnsatisfiedLinkError: dlopen failed:
.../lib/arm64/libnesec-x86.so is for EM_X86_64 (62)
instead of EM_AARCH64 (183)
```

This occurs in `com.netease.nis.wrapper.MyApplication.attachBaseContext` before
Unity renders the game. The APK is not modified and emulator detection is not
bypassed. Per the approved plan, G3/G4 and EmulatorBackend integration stop
here; physical HONOR + scrcpy remains the supported backend.

Raw local crash evidence is stored outside Git at:
`C:\warbot_emulator_poc\g2_data_app_crash.txt`.

## Reproduction

```powershell
python emulator_poc.py create
python emulator_poc.py start --wipe
python emulator_poc.py install
python emulator_poc.py verify
python emulator_poc.py stop
```
