# TUGARIN BOTS — delivery roadmap

Updated: 2026-09-30

The project is now WSA-first. This file distinguishes **implemented code** from
**real-host acceptance**, so a green unit suite is never mistaken for a
production proof.

| Priority | Work item | Code | Real host |
|---|---|---:|---:|
| P0 | WSA runtime + Kingshot gate | DONE | PASS on prior accepted commit |
| P0 | XML/AUMID installer regression | DONE | NEXT smoke |
| P0 | Consoleless GUI/process cleanup | DONE | NEXT smoke |
| P0 | Full Windows CI incl. Qt GUI | DONE | required green |
| P1 | Continuous H.264 preview + screencap fallback | DONE | H.264 host latency/stability smoke pending |
| P1 | FPS/latency telemetry | DONE | smoke |
| P1 | Full mouse controls | DONE | smoke |
| P1 | Keyboard + Unicode clipboard path | DONE | Unicode host smoke |
| P1 | Audio health + volume/mute control | DONE | smoke |
| P1 | Registration state machine | EXISTING | full real-flow pending |
| P1 | Vision/action-gate safeguards | EXISTING | full real-flow pending |
| P2 | Bounded runtime/game recovery | DONE | fault-injection pending |
| P2 | Heartbeat/health panel | DONE | smoke |
| P2 | Structured event log | DONE | soak pending |
| P2 | Corrupt-state fail-closed + previous snapshot | DONE | smoke |
| P2 | Deterministic long-run-lite tests | DONE | CI |
| P2 | Multi-cycle real-game soak | release/heartbeat/event tooling ready | pending |
| P3 | Consolidate production history into main | pending CI/host gate | — |
| P3 | Archive old emulator research paths | pending merge | — |
| P3 | Stable installer/release tag | installer/release gate DONE | tag pending final host evidence |

## Next real-host acceptance command

The code-side hardening is intentionally driven through the existing evidence
workflow. On the target Windows PC the acceptance path remains:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_and_report.ps1
```

The report must prove the newest commit and no console/zombie regression before
a 1.0 tag is created.

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
