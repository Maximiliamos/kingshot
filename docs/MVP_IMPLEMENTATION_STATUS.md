# TUGARIN BOTS — MVP engineering status

Updated 2026-10-08. The integration target is Draft PR #6,
`feature/unified-android-backend`; `main` must NOT be merged/tagged
until a verified exact-SHA, real-Windows-host full acceptance PASS exists.

## Implemented (source-level; CI approval is tracked separately)

1. **Non-destructive resource readiness**: `warbot_cli.py resource-readiness`
   checks two separated production game UI frames and game process existence,
   checks stop/resource-error indications, and emits
   `debug/resource-readiness.json`. It does not click, restart the game,
   change networking, or clear application data. `verify_mvp_full.ps1`
   requires this evidence before any destructive character flow.
2. **Real-frame replay framework**: `vision_replay.py` runs eight reviewed
   real-host fixture images through `TutorialPerception`, compares
   panels/roles/target boxes and rejects unsafe actions on negative frames.
   Account-restriction frames verify the same terminal-stop precedence used
   by `bot.py`; missing frames and checksum mismatches fail. The strict gate
   currently covers four positive and four negative cases.
3. **Declarative Task Engine (incremental)**: `task_engine.py` centralizes
   role-to-panel/action/next-step mapping and delegates retry budgets to
   `BoundedActionPolicy`. Battle, construction, resident source, resident
   assignment and resident-completion paths use it when semantic perception
   is available; legacy image fallbacks remain bounded for older scenes.
4. **CI main push trigger**: `poc-tests.yml` now has `push.branches: main`,
   parses the new PowerShell verifiers, compiles new modules and runs tests.
   This takes effect for main after integration merge.
5. **Run-isolated evidence**: `run_full_mvp_and_report.ps1` now exports
   only `events.jsonl` entries matching `acceptance_run_id` and masks
   common tokens/emails; cumulative `bot.log` is never published.

## Open release blockers (must not be marked PASS)

- **External Kingshot resource download**: observed failure on exact-host
  acceptance `20dce2fc11a943c3a350df1bc35f39da`. CI cannot prove this is
  fixed. The readiness gate proves *visible UI before reset*, not continued
  availability of all resources after `pm clear`.
- **Replay coverage is bounded, not exhaustive**: the strict corpus has four
  positive and four negative redacted real-host frames. Expand it whenever a
  new panel/unknown state is observed; do not count synthetic frames as host
  evidence.
- **Exact-State #3 → tutorial → rename and two soak cycles**: host-only,
  evidence still missing. Do not modify the release gate to skip these checks.
- **Full vision/task migration**: special-purpose resident/source/construction
  detectors still exist as fallbacks. Migrate only after the real-frame corpus
  protects transitions.
- **Distribution**: WSA support has ended; migration to a maintained
  Android emulator and installer/rollback are post-MVP, not grounds to skip
  WSA host acceptance.

## Operator sequence (dedicated Windows account)

From the intended dedicated WSA Windows session, with all relevant data
backed up, inspect Kingshot in a NORMAL run. Do not immediately clear data:

```powershell
cd C:\warbot_git
& "C:\warbot_wsa\tugarin-venv\Scripts\python.exe" .\warbot_cli.py resource-diagnostics --backend wsa --serial 127.0.0.1:58526
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify_resource_readiness.ps1
```

When it fails, collect the read-only evidence and consult
`docs/RESOURCE_LOADING_TRIAGE.md`; do not increase the confirmed retry
limit and do not reset/reinstall without an explicit data-preservation plan.

Next build `tests/fixtures/tutorial_replay/` from safely reviewed real
frames. See `docs/REPLAY_DATASET.md`. Once replay coverage and routine
resource loading are verified, run the exact-head full gate:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_full_mvp_and_report.ps1
```

The full gate **will clear Kingshot data** during game-flow/soak; this may
trigger a new resource download. PASS requires `overall=pass`, exact SHA,
distinct `run_id`, confirmed nickname commits, ordered cycles and audits.
Only then mark PR #6 Ready, merge, check post-merge main CI and tag v1.0.0.
