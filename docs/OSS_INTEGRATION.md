# Open-source integration decisions

WAR BOT reuses proven architecture patterns without vendoring third-party source.

| Project | What WAR BOT adopts | What WAR BOT does not adopt |
|---|---|---|
| Genymobile/scrcpy | transport separation; legacy diagnostic mirroring; future option for a faster frame provider behind the same interface | desktop-window dependency as the production architecture |
| openatx/adbutils | explicit device-scoped serial; screenshot/shell/install/app-lifecycle style API | mandatory dependency for core transport |
| openatx/uiautomator2 | optional structured Android system-dialog channel | Unity/game interaction through accessibility selectors |
| AirtestProject/Airtest | image-first automation for game UI | second template engine beside the existing OpenCV implementation |
| DeviceFarmer/STF | device health/lifecycle should be separate from scenario logic | remote device-farm server stack for a single local workstation |
| Appium | clear driver/backend boundary | WebDriver stack, which is excessive for the current Unity workflow |
| Cuttlefish / ReDroid | retained as alternative Android runtime research paths | mixing a second runtime into the active ranchu PoC before the current A/B diagnosis is complete |

## Resulting WAR BOT stack

```text
GUI / CLI
   ↓
DeviceBackend
   ├─ NativeArm64Backend
   └─ AdbDeviceBackend
          ↓
   screenshot + input + shell + app lifecycle
          ↓
OpenCV image recognition
          ↓
safe state machine
```

The optional uiautomator2 bridge is used only when a real Android system UI
needs structured selectors. It is deliberately not part of Unity game
recognition.

## Performance path

The first working backend uses `adb exec-out screencap -p` because it is
simple and deterministic. Once the native ARM64 guest passes A1-A5, profile:

- screenshot latency;
- tap-to-frame latency;
- CPU usage;
- black/invalid frames.

Only if that path is too slow should a scrcpy-server frame provider be added.
Because capture sits behind `DeviceBackend.frame()`, that optimization will
not require another state-machine rewrite.
