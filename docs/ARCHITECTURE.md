# TUGARIN BOTS architecture

Updated: 2026-10-06

## Production decision

Production runs entirely in the interactive Windows session
`COMPUTER\Программист1`:

```text
Программист1
├─ WSA Android 13 + GApps
│  └─ Kingshot
├─ ADB 127.0.0.1:58526
└─ TUGARIN BOTS
   ├─ PrintWindow capture
   ├─ WsaBackend host input
   ├─ OpenCV perception primitives
   ├─ TutorialPerception / ScreenModel
   ├─ bounded action policy
   ├─ fail-closed state machine
   └─ GUI / evidence / recovery
```

A separate `TugarinBots` Windows user is not a production dependency.

## Capture

The production source is the exact visible Kingshot HWND. The
`WsaGameWindowCapture` transport:

1. enumerates the unique Kingshot top-level window;
2. reads its client rectangle;
3. renders that HWND through Win32 `PrintWindow` with client/full-content
   flags;
4. converts the temporary GDI bitmap to BGR;
5. restores/deletes every selected object/DC/bitmap on all success/failure
   paths.

It is reported as `wsa-window` with `capture_method=printwindow`.
Production automation and GUI both use this path. Overlapping desktop windows
therefore cannot become the vision source.

MSS/scrcpy/H.264/ADB PNG remain diagnostic code paths only and are not required
by production release gates.

## Coordinate model and input

Vision crops the portrait game content from the WSA client frame. Its input
geometry is kept **client-relative**:

```text
vision normalized point
→ WSA client content rectangle
→ WsaBackend
→ add ClientToScreen window origin
→ Win32 cursor/down/move/up
```

`WsaBackend` briefly foregrounds the real Kingshot HWND so Unity accepts the
pointer, then restores the previous cursor position and foreground window.
A synthetic left button is always released in `finally`, including a failed
mid-swipe. If the exact host-input path is unavailable, tap/hold/swipe fail
closed; they do not silently fall back to an unfocused ADB tap.

While automation owns capture, the GUI reads a replaceable JPEG mailbox plus an
atomic metadata file containing the same client-relative viewport, preventing a
second capture pipeline and preventing GUI manual input from reverting to ADB
framebuffer coordinates.

## Android control channel

ADB remains explicit and device-scoped. It is used for:

- health and `sys.boot_completed`;
- app lifecycle;
- package manager and permissions;
- network/audio diagnostics;
- UI hierarchy / Unicode clipboard path;
- controlled recovery.

Game pointer input is the host-window path described above.

## Tutorial perception

Tutorial automation no longer treats each city background as a separate hand
template. `tutorial_vision.py` builds a small fail-closed `ScreenModel` from
reusable primitives:

- `TutorialGuidanceDetector` finds the illuminated target using glow, pointer
  colours and temporal motion between adjacent frames;
- novel guidance requires temporal evidence; two background-independent core crops
  remain only as a throttled migration fallback;
- `UniversalButtonDetector` detects button geometry/style, while panel context
  assigns semantic roles such as resident/source/construction actions;
- grey is not synonymous with disabled: construction context can promote a grey
  control to an active `construction_upgrade` hold action;
- OCR text is bound to a nearby button bbox as corroborating evidence and never
  creates an action by itself;
- `BoundedActionPolicy` permits only a bounded retry of the same recognised
  target before failing closed.

The historical scene-specific `tutorial_hand_*` matcher remains available for
offline regression, but production tutorial and rename flows do not call the
full matcher. Unknown tutorial UI produces `tutorial-perception-failure.json`
plus full/normalised/annotated images for the runtime report.

## State machine safety

- state-specific visual evidence precedes game actions;
- action gate requires the expected frame transition;
- exact State #3 needs row + modal + confirm evidence and a tutorial
  postcondition;
- unknown UI stops fail-closed;
- server/account restrictions stop and are never bypassed;
- `Тугарин<N>` commits only after exact OCR and durable screenshot evidence;
- `pm clear` preserves the PC-side nickname counter.

## Recovery and evidence

`RecoveryController` uses bounded budgets. Recovery acceptance uses the same
production PrintWindow capture after game restart and after ADB reconnect.

`verify_mvp_full.ps1` is the authoritative release orchestrator. It requires a
clean tracked tree and exact published upstream SHA, then records per-gate
evidence in `debug/mvp-full-acceptance.json`.

`run_full_mvp_and_report.ps1` packages those files and uploads the result,
including failures, to the `runtime-reports` branch for remote diagnosis.

## Non-production paths

Retained only for research/diagnostics:

- Native ARM64/QEMU;
- BlueStacks/legacy emulator PoCs;
- legacy desktop scrcpy capture;
- WSA scrcpy-server H.264;
- ADB PNG capture.

They must not block or silently replace the PrintWindow production path.
