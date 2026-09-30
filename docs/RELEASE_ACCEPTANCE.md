# TUGARIN BOTS 1.0 release acceptance

This checklist separates code-complete work from evidence that can only be
produced on the target Windows/WSA host.

## Automated hosted gate

GitHub Actions must pass the entire Windows suite with zero skips. The suite
covers compilation, PowerShell parsing, Qt construction, vision/state-machine
regressions, recovery, persistence, frame streaming and installer contracts.

## One-command real-host infrastructure gate

Run from the release branch after closing the GUI:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\verify_release.ps1
```

PASS requires:

- the same Python tests are green on the target host;
- WSA is ready;
- network, Internet, framebuffer, package manager, storage and audio pass;
- Kingshot stays alive on one PID for 120 seconds;
- final screenshot is not the loading logo;
- no stale dedicated-user Python/CMD/conhost process remains.

The resulting heartbeat, screenshots and P0 diagnostics provide evidence for
the release commit.

## Current infrastructure evidence — PASS

Real-host run on 2026-09-30, commit
`5aacd64efdfb2b6d2b609f9a5a25cf93255bba04`:

- local hosted-equivalent suite: **179 tests / OK**;
- WSA backend ready on `127.0.0.1:58526`;
- Android 13, 1920×1080 framebuffer;
- network, Internet, audio and package manager all ready;
- Kingshot stayed on PID 9507 for the full 120-second stability gate;
- startup and final loading-logo checks both false;
- dedicated-user process audit: **PASS**, no stale Python/CMD/conhost;
- disabled historical GApps migration task is non-running.

Therefore the one-command infrastructure gate is complete for this release
candidate. Remaining acceptance is product-behavior evidence: interactive
preview/input, full registration flow, recovery fault injection and soak.

## H.264 preview acceptance

After the infrastructure gate passes, validate the real low-latency preview
transport on the same WSA host:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\verify_preview.ps1
```

Target-host diagnostics proved Android `screenrecord v1.3` is file-only on
this WSA build (zero stdout H.264 bytes at 720p/540p/native). It is therefore
not a release transport.

Default acceptance now provisions the pinned official `scrcpy-server v4.1`
(SHA-256 verified), runs it as Android shell **inside WSA**, and requires the
active transport to remain `scrcpy-h264` for the smoke window. FPS plus
average/p95 frame latency are reported. Use `-AllowFallback` only for
diagnostics; PNG `screencap` is not a PASS.

## Full registration-flow acceptance

Start TUGARIN BOTS with a clean explicitly approved game state and record one
complete safe flow:

```text
initial tutorial
→ create exact State #3
→ explicit State #3 confirm
→ character tutorial
→ governor rename
→ Тугарин<N>
→ next-character boundary
```

Acceptance rules:

- every input is preceded by state-specific visual evidence;
- State #3 requires both row and confirm evidence;
- action gate observes the expected frame change;
- nickname number is preserved PC-side;
- no generic/blind close or confirm action is used;
- an unknown screen stops fail-closed.

Evidence to retain:

- `logs/events.jsonl`;
- `debug/runtime-heartbeat.json`;
- screenshots for any unknown/action timeout;
- final `state.json` and `state.previous.json`.

## Recovery acceptance

Inject these non-destructive failures one at a time:

1. stop Kingshot while the bot is waiting;
2. interrupt ADB transport temporarily;
3. resume ADB with WSA still healthy.

PASS:

- transient capture failures retry;
- dead game is relaunched only within the configured bounded budget;
- persistent runtime loss stops;
- unknown UI never triggers blind recovery clicks.

## Soak acceptance

Run multiple normal registration cycles on the real host. The objective is not
a synthetic number of clicks but stable resources and deterministic state.

Track:

- completed characters/cycles;
- game restarts;
- recovery events;
- unknown screens;
- frame FPS/latency;
- heartbeat age;
- memory/process growth;
- final nickname counter.

A 1.0 tag should not be created while any release-gate failure remains
unexplained.

## One-command MVP 1.0 host acceptance

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\verify_mvp_full.ps1
```

The orchestrator is fail-fast and runs:

```text
verify_release.ps1
→ verify_preview.ps1 (scrcpy-h264 required)
→ verify_recovery.ps1 (stop Kingshot + ADB disconnect/reconnect)
→ verify_game_flow.ps1 (exact State #3 → tutorial → Тугарин<N>)
→ verify_soak.ps1 (minimum two one-character reset cycles)
```

The game-flow and soak stages intentionally clear Kingshot application data;
the PC-side nickname counter is preserved.

## Release sequence

```text
hosted CI green
→ verify_mvp_full.ps1 PASS on the exact release commit
→ merge/consolidate into main
→ archive old emulator research PRs/branches
→ tag 1.0
```
