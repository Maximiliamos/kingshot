# TUGARIN BOTS progress

Updated: 2026-09-30

## Proven on the real Windows host

The latest published WSA P0 evidence before this hardening branch proved:

- Windows 10 WSA registration and Android 13 boot;
- authorized ADB at `127.0.0.1:58526`;
- framebuffer capture;
- package manager and storage;
- Android network + validated Internet;
- Android audio service;
- all three Kingshot APK splits installed;
- Kingshot stable on one PID for 120 seconds;
- startup/final frames no longer classified as loading;
- Google services installed and interactive Google authorization completed.

That evidence was produced at commit `b565c8d`. The later console-window fixes
were code/CI green but still require one final real-host smoke on the newest
hardening HEAD.

## Implemented on feature/tugarin-bots-v1-hardening

### P0 hardening

- fixed Windows PowerShell XML parsing by reading AppxManifest with `-Raw`;
- primary AUMID discovery now reads the installed AppxManifest directly;
- critical WSA installer is parsed by CI;
- CI installs PySide6 and constructs/closes the real GUI offscreen;
- tutorial vision regression no longer silently skips;
- runtime and CI dependency versions are pinned.

### P1 application

- WSA is the explicit production backend;
- single continuous GUI frame worker;
- FPS/latency transport metrics in the GUI;
- click/swipe/hold/wheel/right-click input;
- ASCII input plus optional Unicode clipboard/paste;
- Android volume up/down/mute controls;
- network/Internet/audio/P0/runtime identity health panel.

### P2 resilience

- bounded capture/runtime/game recovery;
- atomic state writes plus previous snapshot;
- corrupt state is preserved and stops fail-closed;
- structured JSONL event stream;
- runtime heartbeat file surfaced in GUI;
- deterministic long-run-lite persistence/event tests.

## Current gates

| Gate | State |
|---|---|
| WSA boot/game P0 on previously proven commit | PASS |
| Latest hosted Windows CI | MUST BE GREEN before merge |
| Latest hardening HEAD real-host smoke | PENDING HOST |
| Console flash/zombie-process host smoke | PENDING HOST |
| Full State #3 -> tutorial -> rename real-game cycle | PENDING HOST |
| Multi-cycle unattended real-game soak | PENDING HOST |
| True compressed low-latency video transport | TRANSPORT BOUNDARY READY; HOST IMPLEMENTATION/ACCEPTANCE PENDING |

## Definition of product-ready 1.0

Code may be merged when hosted CI is fully green. A 1.0 tag should additionally
require real-host evidence for:

1. consoleless GUI start/stop;
2. no stale dedicated-user probe processes;
3. complete registration flow through exact State #3 and `Тугарин<N>`;
4. restart recovery;
5. multi-cycle soak.
