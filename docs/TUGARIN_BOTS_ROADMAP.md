# TUGARIN BOTS — delivery roadmap

Updated: 2026-09-30

The project is now WSA-first. This file distinguishes **implemented code** from
**real-host acceptance**, so a green unit suite is never mistaken for a
production proof.

| Priority | Work item | Code | Real host |
|---|---|---:|---:|
| P0 | WSA runtime + Kingshot gate | DONE | PASS on 2026-09-30 release-candidate host run |
| P0 | XML/AUMID installer regression | DONE | PASS in release host gate |
| P0 | Consoleless GUI/process cleanup | DONE | process audit PASS; pythonw + rendered-frame host gate automated |
| P0 | Full Windows CI incl. Qt GUI | DONE | required green |
| P1 | WSA-internal scrcpy-server H.264 + screencap fallback | DONE | replacement host latency/stability smoke pending |
| P1 | FPS/latency telemetry | DONE | smoke |
| P1 | Full mouse controls | DONE | smoke |
| P1 | Keyboard + Unicode clipboard path | DONE | Unicode host smoke |
| P1 | Audio health + volume/mute control | DONE | smoke |
| P1 | Registration state machine | EXISTING | full real-flow pending |
| P1 | Vision/action-gate safeguards | EXISTING | full real-flow pending |
| P2 | Bounded runtime/game recovery + periodic PID health | DONE | automated game-stop + ADB-disconnect host gate pending |
| P2 | Heartbeat/health panel | DONE | smoke |
| P2 | Structured event log | DONE | soak pending |
| P2 | Corrupt-state fail-closed + previous snapshot | DONE | smoke |
| P2 | Deterministic long-run-lite tests | DONE | CI |
| P2 | Multi-cycle real-game soak | one-character reset cycles + machine-readable evidence DONE | host run pending |
| P3 | Consolidate production history into main | pending CI/host gate | — |
| P3 | Archive old emulator research paths | pending merge | — |
| P3 | Stable installer/release tag | installer/release gate DONE | tag pending final host evidence |

## Next real-host acceptance command

The complete MVP path is now one fail-fast command:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\verify_mvp_full.ps1
```

It first verifies a clean tracked Git worktree, then runs infrastructure,
low-latency preview, real GUI/operator I/O, recovery fault injection, one exact
State #3 → `Тугарин<N>` cycle, and a bounded multi-cycle soak. The complete
result is persisted to `debug/mvp-full-acceptance.json`.

## 1.0 release gates

```text
Hosted CI                  PASS
WSA + Kingshot P0          PASS on release commit
GUI console smoke          PASS
Manual input smoke         PASS
Unicode input smoke        PASS
State #3 full flow         PASS
Tugarin<N> rename          PASS
Game restart recovery      PASS
WSA/ADB reconnect          PASS
Multi-cycle soak           PASS
```
