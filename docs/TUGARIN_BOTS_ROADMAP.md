# TUGARIN BOTS — development plan

TUGARIN BOTS is the product name. Existing `WAR_BOT_*` environment variables
and internal file names remain temporarily for backward compatibility while the
runtime is stabilised.

| Priority | Stage | Implementation | Result |
| --- | --- | --- | --- |
| P0 | Android runtime gate | Finish Windows Android runtime startup and local control-channel readiness | Stable Android runtime on the target PC |
| P0 | Kingshot gate | Install the three verified APK parts, launch Kingshot, pass the historical ~80% loading point and keep the process stable | Proves the selected runtime is viable |
| P0 | Runtime services | Validate Android boot, framebuffer, network route, Internet reachability and audio service | One health snapshot for everything the game needs |
| P0 | Diagnostics | Capture screenshot, process state and crash logs on every failed gate | Evidence-driven fixes instead of guesswork |
| P1 | TUGARIN BOTS branding | Rename user-facing GUI, CLI banners and documentation; retain old internal env names during migration | Product identity without breaking existing host scripts |
| P1 | Interactive screen | Show Android framebuffer inside the app; mouse click -> tap, drag -> swipe, focused keyboard -> Android keys/text | Manual emulator control without leaving TUGARIN BOTS |
| P1 | Manual/automatic arbitration | Automatically pause bot logic when the user manually controls the embedded screen | Prevents bot clicks and human clicks from fighting |
| P1 | Low-latency video | Replace polling screenshot preview with a continuous video transport once the runtime gate is stable | Smooth near-real-time screen inside the app |
| P1 | Audio forwarding | Route Android game audio to the Windows host and expose mute/volume state in TUGARIN BOTS; live embedded audio transport is paired with the low-latency stream | Game sound available alongside the embedded screen |
| P1 | Keyboard completeness | Support navigation keys immediately; add verified Unicode text path for Cyrillic after runtime acceptance | Full keyboard control including game text fields |
| P1 | Network integration | Use the Windows-host network provided by the Android runtime, validate route and Internet access, and surface failures in GUI | No separate network setup for the user |
| P1 | Game controls | Validate tap/swipe/hold/key input against the real Kingshot framebuffer | Reliable coordinates at actual device resolution |
| P1 | Vision calibration | Re-validate OpenCV templates on real runtime screenshots | Bot understands the same screens it can control |
| P1 | Registration workflow | Restore full State #3 / tutorial / Tugarin<N> cycle on the accepted runtime | Working end-to-end automation |
| P2 | Recovery | Reconnect transport, relaunch game, fail closed on unknown screens | Resilient long-running bot |
| P2 | GUI health panel | Show runtime/game/network/audio status and last failure | Immediate operator visibility |
| P2 | Long-run tests | Multiple cycles, game restarts and Windows restarts | Confidence in unattended operation |
| P3 | Cleanup | Demote old QEMU/BlueStacks experiments to diagnostic fallback and update PR/README | Simpler maintainable codebase |
| P3 | Packaging | One installer/shortcut, product assets, stable tag | Finished TUGARIN BOTS MVP |

## Current P0 status — 2026-09-28

Code-side P0 is complete and covered by CI. Real-host acceptance is still required before P1 may begin.

- Windows 10 WSA package installation/registration: **real-host PASS**.
- Developer/control channel: installer opens developer settings, retries for four minutes, supports one-time pairing, and recycles the subsystem once.
- Windows prerequisites: `VirtualMachinePlatform` + `HypervisorPlatform` are enforced; hypervisor launch is checked.
- Runtime gate: `sys.boot_completed=1`, real framebuffer, package manager, >=1024 MiB free `/data`, network route, validated Internet, and Android audio service.
- Kingshot gate: verified split install, process launch, stable PID for 120 seconds, startup/final screenshots, and verified `loading_logo.png` must be absent at the end.
- Failure bundle: crash buffer, logcat tail, package path, PID, connectivity, audio, process dump, full health JSON, failure screenshot, port 58526 and excluded-range diagnostics.
- **Do not begin P1 until a real-host run reports `WSA_GAME_PASS`.**

## Acceptance milestones

### M1 — Runtime PASS
- Android boot complete.
- Local control channel online.
- Framebuffer screenshot valid.
- Network route present and Internet reachable.
- Android audio service available.

### M2 — Kingshot PASS
- All APK parts installed.
- Kingshot launches.
- Historical ~80% loading point is passed.
- Process remains stable for at least 120 seconds.
- A real game screenshot is captured.

### M3 — Interactive GUI PASS
- Screen is visible inside TUGARIN BOTS.
- Mouse click maps to tap.
- Mouse drag maps to swipe.
- Navigation and printable keyboard input work.
- Manual input pauses automation to avoid conflicting actions.

### M4 — Streaming PASS
- Continuous low-latency video replaces polling screenshots.
- Android game audio is forwarded to the Windows host.
- Mute/volume and transport failures are visible in the GUI.

### M5 — Automation MVP
- Vision templates validated.
- State #3 workflow validated.
- Tutorial and Tugarin<N> naming validated.
- Multiple cycles complete with fail-closed recovery.
