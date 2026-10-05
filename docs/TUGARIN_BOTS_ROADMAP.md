# TUGARIN BOTS — delivery roadmap

Updated: 2026-10-05

The project is in release-closure mode. WSA/GApps under
`COMPUTER\Программист1` remains the production Android runtime; no new
emulator/runtime architecture should be introduced without a concrete failure.

| Priority | Work item | Code | Final-head real host |
|---|---|---:|---:|
| P0 | WSA/GApps + Kingshot | DONE | rerun in full gate |
| P0 | Google Services acceptance | DONE | rerun in full gate |
| P1 | exact HWND PrintWindow capture | DONE | PENDING |
| P1 | >=15 FPS + latency/black/frozen/error evidence | DONE | PENDING |
| P1 | tap/hold/swipe client mapping + restore/fail-close | DONE | game-flow proof PENDING |
| P1 | consoleless GUI + shared-frame client geometry | DONE | PENDING |
| P1 | exact tutorial / State #3 state machine | DONE | PENDING |
| P1 | `Тугарин<N>` OCR + screenshot/hash commit | DONE | PENDING |
| P2 | bounded game + ADB recovery | DONE | PENDING |
| P2 | atomic state / heartbeat / structured events | DONE | PENDING |
| P2 | minimum two-character soak | DONE | PENDING |
| P2 | stale-process/resource audit | DONE | PENDING |
| P3 | full evidence uploader | DONE | PENDING |
| P3 | PR merge + v1.0.0 | BLOCKED | after host PASS |

## Remaining path

```text
hosted CI green
→ run_full_mvp_and_report.ps1 on Программист1
→ inspect first failing gate, if any
→ patch only that gate and rerun
→ mvp-full-acceptance.json overall=pass
→ PR #6 Ready
→ main
→ main CI
→ v1.0.0
```

The local host is now the source of new tutorial templates. If a previously
unseen screen appears, preserve its screenshot and stop fail-closed; do not add
blind coordinates or generic close/confirm behavior.
