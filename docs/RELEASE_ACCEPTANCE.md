# TUGARIN BOTS 1.0 release acceptance

Updated: 2026-10-05

This checklist separates hosted/code acceptance from evidence that requires the
real Windows/WSA/Kingshot host.

## 1. Hosted gate

GitHub Actions must pass the complete Windows suite with zero failures and zero
skips. It covers Python compilation/tests, PowerShell parsing, Qt construction,
vision/state-machine regressions, PrintWindow cleanup, WSA host-input mapping,
recovery, persistence, installer packaging and acceptance-script contracts.

## 2. Exact source gate

The real-host run must start from:

- clean tracked worktree;
- `COMPUTER\Программист1` / expected SID;
- local branch HEAD equal to its published upstream HEAD.

`verify_mvp_full.ps1` fails closed before destructive game-flow work if these
conditions are not met. A gate that exits 0 but fails to leave any declared
evidence file is also converted to a release failure.

## 3. Full host sequence

The orchestrator is fail-fast:

```text
Infrastructure / WSA / Kingshot / process audit
→ Google Services / Play Store / account
→ production PrintWindow preview
→ consoleless GUI render
→ Unicode/UI/audio operator channel
→ bounded Kingshot + ADB recovery
→ exact State #3 → tutorial → Тугарин<N>
→ minimum two-character reset/soak
→ final process audit
```

### Infrastructure

PASS requires ready WSA/ADB, network/Internet/audio/package manager/storage,
stable Kingshot PID for the bounded stability gate and a non-loading final
frame.

### Google Services

PASS requires enabled `com.google.android.gms`, `com.android.vending`,
`com.google.android.gsf`, a Google account presence boolean, Play Store
foreground launch without crash-loop/ANR, a real UI frame and cleanup of the
auxiliary Android windows. Account names/tokens are not persisted.

### Production capture

Release capture is `wsa-window` backed by Win32 `PrintWindow` against the
exact Kingshot HWND. It does not use MSS desktop pixels and does not depend on
scrcpy-server. PASS requires at least 15 sustained FPS, zero black-frame
failure, no stale stream and zero capture errors. Metrics and the final frame
are persisted.

### Input

WSA game tap/hold/swipe are client-relative and injected through the real game
HWND. The implementation restores the user's cursor and previous foreground
window and releases the synthetic mouse button even on a mid-gesture failure.
If this path is unavailable, game input fails closed rather than silently
falling back to unfocused ADB taps. The real game-flow gate is the functional
proof that these inputs are accepted by Kingshot.

### Exact State #3 and rename

The flow is:

```text
initial tutorial
→ exact State #3 row
→ exact State #3 modal + confirm
→ new-character tutorial visible
→ tutorial complete
→ governor rename
→ exact Тугарин<N> OCR
→ post-rename screenshot/crop + hashes
```

The nickname counter advances only after the evidence-backed commit.

### Recovery

The gate stops Kingshot, requires bounded restart and a production PrintWindow
frame, then disconnects/reconnects ADB and again requires ready Android plus a
production frame. Persistent loss stops the run.

### Soak

At least two one-character cycles are required. The evidence must contain
ordered `Тугарин<N>` commits, a post-rename screenshot for each, at least one
cycle reset, no terminal stop reason and resource/process telemetry. The soak
records initial/final-observed/peak bot working set, requires a runtime
heartbeat to be observed, and fails closed if heartbeat age exceeds 30 seconds.

## 4. Evidence files

A successful run produces or collects:

```text
debug/mvp-full-acceptance.json
debug/google-services.json
debug/google-services.png
debug/preview-production.json
debug/preview-production.png
debug/gui-host-smoke.json
debug/operator-io-smoke.json
debug/recovery-smoke.json
debug/mvp-game-flow-evidence.json
debug/mvp-soak-evidence.json
debug/mvp-final-process-audit.json
debug/runtime-heartbeat.json
logs/events.jsonl
```

## 5. Recommended one-command run + upload

```powershell
cd C:\warbot_git
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\scripts\run_full_mvp_and_report.ps1"
```

The runner pulls the branch with `--ff-only`, re-execs itself if updated,
requires exact upstream equality, runs `verify_mvp_full.ps1`, collects PASS
or FAIL evidence and uploads it to the `runtime-reports` branch.

Direct run without upload remains available:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\scripts\verify_mvp_full.ps1"
```

The game-flow and soak stages intentionally clear Kingshot application data;
the PC-side nickname counter is preserved.

## 6. Release rule

Do not merge/tag while any required host gate is unproven.

```text
hosted CI green
→ exact-head full host overall=pass
→ PR #6 Draft → Ready
→ merge to main
→ main CI green
→ tag v1.0.0
```
