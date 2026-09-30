# TUGARIN BOTS architecture

Updated: 2026-09-30

## Production decision

The production Windows runtime is **Windows Subsystem for Android (WSA)**.
Native ARM64 QEMU, BlueStacks and the old Android Emulator are retained only as
diagnostic/research paths and must not drive normal product behavior.

```text
TUGARIN BOTS GUI
  ├─ continuous preview worker + manual input
  ├─ runtime/game/network/audio health
  └─ operator controls
          │
          ▼
DeviceBackend
  ├─ WsaBackend                 <- production
  ├─ AdbDeviceBackend           <- compatibility/debug
  └─ NativeArm64Backend         <- experimental fallback
          │
          ├─ screenshot/input/app lifecycle
          └─ optional uiautomator2 system-UI/Unicode channel
          │
          ▼
OpenCV vision + action gate
          │
          ▼
Fail-closed game state machine
```

## Runtime acceptance

WSA is accepted only after all required runtime services are proven:

- ADB state is `device`;
- `sys.boot_completed=1`;
- a real framebuffer PNG is decoded;
- package manager responds;
- at least 1024 MiB is free in `/data`;
- Android reports a usable network;
- Internet is validated/reachable;
- Android audio service responds;
- Kingshot starts and remains on one PID through the stability gate.

WSA is allowed to expose x86/native-bridge translation. The no-x86/no-bridge
gate applies only to the experimental Native ARM64 backend.

## Capture and preview

Automation consumes frames through the backend contract. The GUI uses
`ContinuousFrameStream`, a single long-lived preview worker, instead of
creating a new worker for every Qt timer tick.

The GUI now prefers `adb exec-out screenrecord --output-format=h264 -` decoded
continuously by FFmpeg. If FFmpeg/screenrecord is unavailable or the stream
fails, `FallbackCapture` demotes preview to the proven `adb exec-out screencap
-p` path. Automation is independent of GUI preview transport. The H.264 path
is code-complete but still requires final latency/stability measurement on the
target WSA host.

## Input

The backend provides:

```text
tap
swipe
hold
keyevent
ASCII text
Unicode clipboard/paste when uiautomator2 is available
volume up/down/mute
app launch/stop/clear
```

Manual GUI control automatically pauses game automation before sending input.
Right-click maps to Android Back; mouse hold maps to long-press; wheel maps to a
vertical swipe.

## State durability

Runtime state is PC-side and independent of WSA userdata.

- writes are atomic through temporary-file replacement;
- the previous valid snapshot is retained as `state.previous.json`;
- unreadable/corrupt `state.json` is preserved as
  `state.corrupt.<timestamp>.json`;
- corrupt state **stops** the bot instead of silently resetting to defaults;
- `pm clear` never resets the PC-side nickname counter.

## Recovery

`RecoveryController` handles transport/game failures with bounded budgets.

- transient capture failures retry;
- repeated failures trigger a runtime health probe;
- if Android is ready but Kingshot is dead, Kingshot may be relaunched a small
  bounded number of times;
- persistent runtime loss or exhausted restart budget stops the bot;
- unknown game screens are never auto-recovered with blind clicks and remain
  governed by the visual fail-closed watchdog.

## Observability

Two logs exist for different consumers:

- `logs/bot.log` — human-readable operator log;
- `logs/events.jsonl` — structured events.

`debug/runtime-heartbeat.json` records PID, backend, serial, phase, step and
frame/action ages. The GUI surfaces heartbeat age together with network,
Internet, audio and P0 state.

## Safety rules

- all ADB commands are scoped to one explicit serial;
- no blind generic confirm/close actions;
- actions require state-specific visual evidence;
- action gate requires visual change after click/tap when applicable;
- server/account restrictions stop the workflow;
- no Play Integrity spoofing, APK patching, Frida/Magisk-based evasion or
  character-limit bypass belongs in the automation layer;
- GUI is single-instance and launched consolelessly through `run_gui.vbs`.

## Legacy paths

The following code is retained for diagnostics/history but is not production:

- `emulator_poc.py`;
- `bluestacks_poc.py`;
- `native_arm64_poc.py`;
- legacy scrcpy desktop-window capture (diagnostics only);
- production WSA-internal scrcpy-server H.264 transport (no external scrcpy window).

These paths should be moved out of the normal operator surface after the final
WSA host acceptance and repository consolidation.
