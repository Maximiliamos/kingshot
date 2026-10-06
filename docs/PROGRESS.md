# TUGARIN BOTS progress

Updated: 2026-10-06

## Source of truth

Integration branch: `feature/unified-android-backend` / Draft PR #6.

The branch is code-complete only when hosted Windows CI is green. Product-ready
1.0 additionally requires `MVP 1.0 HOST ACCEPTANCE PASS` from the exact
published branch HEAD on the real `COMPUTER\Программист1` WSA host.

## Proven real-host baseline

Latest published infrastructure report before the final PrintWindow hardening:

- report: `runtime-reports/20261005-144548-144ba16`;
- commit: `144ba1607f0717e615d9ede41845ca0380c665cc`;
- WSA LTS 8 / Android 13 / GApps;
- ADB `127.0.0.1:58526` = `device`, `boot_completed=1`;
- Kingshot PID 4350 remained stable for 120 seconds;
- 1920×1080 framebuffer;
- network, Internet, audio and package manager ready;
- startup/final loading-logo checks false;
- result: `WSA_GAME_PASS`.

This proves the infrastructure baseline, not the current final-head MVP.

## Hosted code checkpoint

The complete Windows CI suite passed at code checkpoint
`89ea4b04466f9c700c745f44aaa1bb163693f0cb`:

- **279/279 tests OK**;
- zero test failures;
- PrintWindow cleanup/failure-path tests green;
- WSA client-relative tap/hold/swipe + cursor/foreground restoration tests green;
- Google/full-MVP/recovery/soak acceptance contracts green.

Documentation-only commits after this checkpoint still require their normal
GitHub Actions rerun before handing the branch to the local host.

## Implemented after that baseline

- tutorial vision refactored into `tutorial_vision.py` with `ScreenModel`,
  reusable guidance/button/panel detectors and bounded action policy;
- production tutorial guidance uses glow + pointer colours + temporal motion instead
  of scanning all historical scene-specific hand screenshots;
- two core hand crops remain only as throttled migration fallback; the full historical
  matcher is regression-only and is no longer called by tutorial/rename production paths;
- construction controls distinguish generic primary actions from semantic upgrade holds,
  including active grey hold-buttons;
- OCR is bound to detected button geometry as evidence, never promoted to blind action;
- unknown tutorial states persist machine-readable perception JSON plus annotated/raw
  frames, and the full host reporter collects those diagnostics;
- clean installer now ships `tutorial_vision.py` and no longer requires the legacy
  `upgrade_button.png` asset for production validation;
- exact Kingshot HWND capture via Win32 `PrintWindow`;
- `wsa-window` is now the production capture for both GUI and bot vision;
- no MSS desktop-rectangle dependency for production capture;
- GDI handles are released on success and failure paths;
- WSA game input is client-relative host input with cursor/foreground restore;
- tap/hold/swipe are fail-closed when the real game HWND/input path is absent;
- bot-shared GUI frames publish matching WSA-client geometry;
- Google Services acceptance checks Play Services, Play Store, account presence,
  foreground launch, ANR/crash-loop state and UI evidence without persisting
  account identity;
- release preview requires exact PrintWindow transport, >=15 FPS, zero capture
  errors and persists JSON + PNG evidence;
- recovery acceptance uses the same production capture as automation;
- Google/preview/GUI/operator/recovery/flow/soak/process-audit evidence paths are
  recorded by the full orchestrator;
- full acceptance rejects a local SHA that is not the published upstream SHA;
- every successful host gate must leave its declared evidence file(s), otherwise the full run fails closed;
- final process audit now rejects project-owned stale Python/CMD/conhost/FFmpeg/PowerShell helpers and active legacy migration/continuation tasks;
- scrcpy-server is diagnostic only and is no longer a production host-gate
  dependency;
- `run_full_mvp_and_report.ps1` performs the entire real-host acceptance,
  inventories every collected evidence file with size + SHA-256, and uploads
  PASS or FAIL evidence to `runtime-reports`.

## Product-flow safeguards already in code

- exact State #3 row + modal + confirm evidence;
- `state3_confirmed` is emitted only after the modal disappears and the
  new-character tutorial is visible;
- unknown screens and account/server restrictions stop fail-closed;
- no blind generic close/confirm actions;
- nickname `Тугарин<N>` commits only after exact OCR evidence;
- each nickname commit stores screenshot/crop evidence and SHA-256;
- PC-side nickname counter advances only after confirmed commit;
- soak requires at least two ordered nickname commits, screenshots and a reset;
- soak evidence records initial/final-observed/peak bot working set and heartbeat freshness, and fails on a >30-second stale heartbeat;
- `pm clear` preserves the PC-side nickname counter.

## Current release gates

| Gate | Code | Exact final-head real host |
|---|---:|---:|
| WSA/GApps + Kingshot P0 | DONE | rerun required |
| Google Services | DONE | rerun required |
| PrintWindow preview | DONE | rerun required |
| Consoleless GUI render | DONE | rerun required |
| host input / Unicode / audio | DONE | game-flow + host smoke required |
| Kingshot + ADB recovery | DONE | rerun required |
| exact State #3 flow | DONE | PENDING |
| `Тугарин<N>` rename evidence | DONE | PENDING |
| 2+ character soak | DONE | PENDING |
| final process audit | DONE | PENDING |
| full `overall=pass` | DONE | PENDING |
| merge/tag | BLOCKED by host gate | — |

## Final host command

Preferred command because it also publishes evidence:

```powershell
cd C:\warbot_git
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\scripts\run_full_mvp_and_report.ps1"
```

The flow/soak stages intentionally clear Kingshot application data. The
PC-side `Тугарин<N>` counter is preserved.

Only after the exact current-head report has `mvp-full-acceptance.json` with
`overall = "pass"` should PR #6 move from Draft to Ready, merge to `main`,
run main CI and receive tag `v1.0.0`.
