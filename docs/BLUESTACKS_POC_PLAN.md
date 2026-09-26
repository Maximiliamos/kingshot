# BlueStacks 5 PoC — continuation after official AVD G2 FAIL

## Why this exists

The official Android Emulator path is fully recorded in
`docs/EMULATOR_POC_RESULTS.md`:

- native ARM64 AVD cannot boot on the x86-64 Windows host;
- x86_64 Google Play AVD boots and installs the ARM64 APKS;
- G2 fails before Unity in `MyApplication.attachBaseContext` because the
  protected package loads an incompatible native library through Android's
  translation path.

That result does **not** prove that every Windows Android runtime is
incompatible with the game.

BlueStacks publishes a current PC page for «Война за трон» by Echofun
Interactive Limited and BlueStacks 5 supports Android 11 plus configurable
ARM/ARM64 ABI libraries and ADB. Therefore BlueStacks is the next compatibility
PoC.

## Safety boundary

This PoC performs compatibility diagnostics only.

It does not:

- patch `.apk` or `.so` files;
- replace `libnesec`;
- root BlueStacks;
- spoof Play Integrity;
- hide emulator indicators;
- alter the game's protection layer.

If the game refuses a stock supported BlueStacks environment, that result is
recorded as another backend FAIL.

## Required first instance

Create a fresh BlueStacks 5 instance:

- Android: Android 11, 64-bit;
- ABI: Custom with ARM 64-bit enabled (ARM+x86 is acceptable for the first
  experiment if BlueStacks requires a mixed set);
- root: off/default;
- ADB: enabled in Settings -> Advanced;
- orientation: portrait;
- resolution: use the closest supported portrait size; the bot can scale later;
- install the game **from Google Play inside this instance**.

Do not start by sideloading the HONOR-specific APKS. Google Play should be
allowed to choose the package splits for the BlueStacks device profile.

## Gates

### B1 — runtime

- BlueStacks instance starts;
- ADB connects;
- Android version, ABI and native bridge are captured.

### B2 — Play Store package

- `com.got.globalru` is installed from Google Play;
- record versionName/versionCode;
- record `primaryCpuAbi`;
- record every package/split path from `pm path`.

The delivered package may differ from the HONOR ARM64 APKS. That difference is
valuable evidence.

### B3 — bootstrap/render

- start `com.unity3d.player.MyMainPlayerActivity`;
- process remains alive for at least 30 seconds;
- obtain a real frame;
- no early native crash.

On failure, save logcat + Dropbox evidence. Do not patch the package.

### B4 — automation transport

After B3 passes:

- benchmark ADB screencap;
- verify ADB tap/swipe;
- measure tap-to-frame latency;
- only then adapt `DeviceBackend` and GUI.

## CLI

After BlueStacks is installed and the instance exists:

```powershell
python bluestacks_poc.py instances
python bluestacks_poc.py start --instance Rvc64
python bluestacks_poc.py connect --instance Rvc64
python bluestacks_poc.py status --instance Rvc64
python bluestacks_poc.py verify --instance Rvc64
python bluestacks_poc.py capture --instance Rvc64
```

If the internal instance name differs, `instances` reads it from
`C:\ProgramData\BlueStacks_nxt\bluestacks.conf`.

## Fallback matrix

Do not test every combination randomly. Use this order:

1. Android 11 / ARM64-capable instance / Google Play install.
2. Android 11 / mixed x86+ARM / Google Play install.
3. Pie 64 / ARM64-capable / Google Play install.
4. Only after those stock BlueStacks variants fail, evaluate a second Windows
   Android runtime such as MuMu as a separate PoC.

Each result must include actual ADB properties and crash evidence.
