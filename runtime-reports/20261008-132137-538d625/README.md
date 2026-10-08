# Non-destructive resource-readiness report

- Checked source SHA: `538d6258070a4aa4fcc6d81dfd6ef154a0c18a64`
- Windows session: dedicated WSA account preflight had previously passed.
- Run ID: not applicable; the probe is deliberately non-destructive.
- Game data: not cleared. No restart, tap, network, DNS, VPN, proxy, or account change was made.

## Result: FAIL

`warbot_cli.py resource-diagnostics --backend wsa --serial 127.0.0.1:58526`
completed with exit code 0, but correctly reports `external_resource_loading_unresolved`:
it cannot establish Kingshot CDN reachability.

`scripts/verify_resource_readiness.ps1` returned exit code 51. Kingshot process
was present and both captured frames were valid, but each was classified as
`tutorial_dialog` with no confirmed semantic role or tutorial target. No
resource-error dialog and no account restriction was detected. Therefore the
gate reported `resource_readiness_unconfirmed` with `samples_confirmed=0` of 2.

Android diagnostics contained no persisted private DNS hostname, proxy address,
request URL, token, account name, or screenshot. Transport indicators mention
validated connectivity and no VPN transport; all selected game-logcat network
error counters were zero. These signals are diagnostic only and do not prove
resource download success.

## Required next action

Keep Kingshot data intact. Obtain an ordinary, visibly recognized game UI and
repeat the non-destructive readiness gate before any destructive acceptance.
Do not increase the bounded resource-retry budget.
