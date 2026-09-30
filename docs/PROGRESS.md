# TUGARIN BOTS progress

Updated: 2026-09-30

## Proven on the real Windows host

Latest infrastructure host acceptance: **PASS** on 2026-09-30 at
commit `5aacd64efdfb2b6d2b609f9a5a25cf93255bba04`.

This is not the full MVP acceptance. The unified full-host orchestrator has
not yet produced `MVP 1.0 HOST ACCEPTANCE PASS` on the current release
candidate from the exact `TugarinBots` Windows SID.

Evidence:

- dedicated runtime Python `C:\warbot_wsa\tugarin-venv\Scripts\python.exe`;
- 179 hosted-equivalent tests passed locally with `OK`;
- Windows 10 WSA / Android 13 ready on `127.0.0.1:58526`;
- framebuffer resolution 1920×1080;
- network ready and Internet reachable;
- Android audio service ready;
- package manager ready;
- free `/data` space ~121790 MiB;
- Kingshot PID 9507 remained stable for 120 seconds;
- startup/final loading-logo checks both false;
- dedicated-user stale-process audit passed with no Python/CMD/conhost process;
- old GApps migration task exists only in disabled state.

Earlier P0 evidence at `b565c8d` is now superseded by this current-head
release acceptance.

## Implemented on feature/unified-android-backend

### P0 hardening

- fixed Windows PowerShell XML parsing by reading AppxManifest with `-Raw`;
- primary AUMID discovery now reads the installed AppxManifest directly;
- critical WSA installer is parsed by CI;
- CI installs PySide6 and constructs/closes the real GUI offscreen;
- tutorial vision regression no longer silently skips;
- runtime and CI dependency versions are pinned.

### P1 application

- WSA is the explicit production backend;
- pinned scrcpy-server v4.1 bootstrap inside WSA, unique ADB-forward socket per stream and FFmpeg decode; on the current host the encoder starts but emits no H.264 frames, so PNG screencap remains the active bounded fallback;
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
| WSA boot/game P0 on real host | PASS |
| Local hosted-equivalent suite | PASS — 229 tests, zero skips |
| Latest integration HEAD real-host infrastructure gate | PASS at `5aacd64` |
| Dedicated-user stale-process audit | PASS — zero stale processes |
| Full State #3 -> tutorial -> rename real-game cycle | CODE + machine-readable evidence DONE; PENDING HOST |
| Kingshot restart + ADB reconnect recovery | CODE + real-host injector DONE; PENDING HOST |
| Multi-cycle unattended real-game soak | CODE + bounded verifier DONE; PENDING HOST |
| True compressed low-latency video transport | FAIL/PENDING — scrcpy encoder starts but emitted zero frames on the real host; PNG fallback active |

## Current-host findings on 2026-09-30

- a black GUI framebuffer was traced to Android power state `Asleep/OFF` and
  Kingshot not running, not to a decoder failure;
- waking Android, enabling stay-awake and starting the declared activity
  produced a real Kingshot framebuffer and PID;
- the 1280x720 PNG fallback measured roughly 350 ms per capture while the game
  was loading; it is explicitly capped at 2 FPS to avoid saturating WSA;
- GUI and bot no longer run two independent capture/decode pipelines: while
  automation owns capture, GUI consumes the bot's replaceable JPEG mailbox;
- expensive runtime health probes are reduced from every 5-10 seconds to a
  bounded 15/60-second cadence;
- full acceptance now rejects the wrong Windows SID before evidence creation,
  stamps one run id through child evidence, and requires exact post-rename OCR
  before advancing the persistent nickname counter.

## Definition of product-ready 1.0

Code is now organized around one full host orchestrator
(`scripts/verify_mvp_full.ps1`). A 1.0 tag should require PASS on that exact
release commit for:

1. consoleless GUI start/stop;
2. no stale dedicated-user probe processes;
3. complete registration flow through exact State #3 and `Тугарин<N>`;
4. restart recovery;
5. multi-cycle soak.
