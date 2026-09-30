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

## Release sequence

```text
hosted CI green
→ verify_release.ps1 PASS on release commit
→ full State #3/Tugarin flow PASS
→ multi-cycle soak PASS
→ merge/consolidate into main
→ archive old emulator research PRs/branches
→ tag 1.0
```
