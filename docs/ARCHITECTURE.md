# WAR BOT architecture

## Goal

Keep the game workflow independent from Android transport. The state machine
should not know whether the frame came from native ARM64 QEMU, another ADB
device, or the legacy scrcpy window.

## Runtime layers

```text
┌───────────────────────────────────────────────────────────────┐
│                         WAR BOT GUI                           │
│ backend selection · runtime controls · preview · logs · state │
└──────────────────────────────┬────────────────────────────────┘
                               │
                               ▼
┌───────────────────────────────────────────────────────────────┐
│                       game state machine                       │
│ create character · tutorial · rename · safety/action gate     │
└──────────────────────────────┬────────────────────────────────┘
                               │
                               ▼
┌───────────────────────────────────────────────────────────────┐
│                         vision layer                           │
│ OpenCV templates · OCR fallback · unknown-screen fail closed  │
└──────────────────────────────┬────────────────────────────────┘
                               │
                               ▼
┌───────────────────────────────────────────────────────────────┐
│                       DeviceBackend                            │
│ health · frame · tap · swipe · keyevent · shell · app control │
└───────────────┬───────────────────────────────┬───────────────┘
                │                               │
                ▼                               ▼
       NativeArm64Backend                AdbDeviceBackend
       QEMU ARM64 runtime                existing ADB Android
       127.0.0.1:5561
```

Legacy scrcpy remains a diagnostics-only frame source while migration is in
progress. It is not the production target.

## Open-source ideas reused

WAR BOT does not vendor source code from these projects. It adopts only the
parts that fit our use case:

- **adbutils**: one explicit device/serial per session; shell, screenshot and
  app lifecycle belong behind one transport object.
- **uiautomator2**: Android system dialogs are better handled through a
  structured UI channel than through blind coordinates. WAR BOT exposes an
  optional uiautomator2 bridge, while Unity/game UI remains image-driven.
- **scrcpy**: video transport and control transport are independent concerns.
  The game state machine must not depend on the existence of a desktop window.
- **Airtest**: game automation should be image-first. The existing OpenCV
  templates are retained instead of adding a second recognition engine.

Cuttlefish/ReDroid remain alternative runtime research paths if the custom
ranchu runtime proves fundamentally unstable; they are not mixed into the
current production path.

## Backend contract

`device_backend.py` provides:

```text
health()
frame()
tap(x, y)
swipe(x1, y1, x2, y2, duration_ms)
hold(x, y, duration_ms)
keyevent(code)
input_text(value)
shell(...)
launch_app()
stop_app()
clear_app_data()
```

The ARM64 backend adds:

```text
start_runtime()
stop_runtime()
```

All ADB calls are scoped to a single serial. This removes the old risk that a
command is sent to the wrong Android device when multiple transports exist.

## Capture

Production capture path:

```text
ADB exec-out screencap -p
        ↓
PNG bytes
        ↓
OpenCV BGR frame
        ↓
normalize to 421 × 944
        ↓
existing templates/state machine
```

This deliberately avoids desktop-window coordinates. If later profiling shows
that ADB screencap is too slow, a scrcpy-server based frame provider can be
added behind the same `frame()` contract without changing the bot logic.

## System UI vs game UI

Use two different strategies:

- Android permission/settings dialogs: optional uiautomator2 selectors.
- Unity game UI: OpenCV/template recognition.

This avoids forcing UiAutomator onto a Unity scene that often has no useful
Android accessibility hierarchy.

## Safety model

The bot prefers doing nothing over guessing.

Rules:

- action only on expected state-specific evidence;
- no blind generic close/confirm taps;
- unexpected screens are saved;
- action gate requires visual change after an input;
- F8 is the global emergency stop;
- dry-run does not send input;
- native ARM64 mode refuses to start game automation unless:
  - ADB state is `device`;
  - `sys.boot_completed=1`;
  - primary ABI is `arm64-v8a`;
  - ABI list contains no x86;
  - native bridge is empty/none/0;
- server/account restrictions stop the workflow; they are not bypassed.

## Current blocker

The DeviceBackend/application layer is now decoupled from the runtime PoC, but
the native ARM64 Android guest itself is not yet A1/A2 PASS. The current boot
reaches zygote/SurfaceFlinger and then repeatedly crashes `app_process64` in
`libcodec2_vndk.so`.

That blocker belongs to the runtime layer. Once ADB reaches `device` and
`sys.boot_completed=1`, the application can immediately reuse the backend,
preview, input and existing vision/state-machine layers without another major
rewrite.
