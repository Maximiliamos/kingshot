# TUGARIN BOTS — Kingshot resource-loading failure triage

Updated: 2026-10-08. Status: **host MVP still blocked**; no release.

## Confirmed real-host incident

Host run `20dce2fc11a943c3a350df1bc35f39da`, exact SHA
`752a89c6a6ae626fecfb32b0424fdea286069190`, reported PASS for
infrastructure/WSA/Google/PrintWindow/GUI/operator I/O/recovery. It failed
inside the initial tutorial, before exact State #3, nickname commit or soak.
The game displayed **«Не удалось загрузить ресурсы»** with a verified
**«Повторить попытку»** button. Two bounded clicks did not dismiss it.

This is a **confirmed in-game resource-error dialog**, not proof that the
game's CDN is down, not an Android network acceptance failure, and not evidence
of a broken PrintWindow capture. The exact network reason is still unknown.

## Check the installed Kingshot version before changing app data

On 2026-10-08 the operator reported that a newer official Kingshot version
exists than the one currently installed. The screenshot of the blocked host
session shows in-game version `1.12.10.1`. This is a **plausible** explanation
for the resource-loading failure, not a confirmed root cause.

On the dedicated Windows/WSA host:

1. Record the displayed game version and installed Android package
   `versionName` / `versionCode` using the known Kingshot package identifier
   and read-only package inspection. Do not assume the displayed number is
   identical to the Android package version.
2. Compare with the version offered by the **official** store for this
   specific Android installation, region and account. Do not invent a latest
   version number or use unverified APK mirrors.
3. If the official store offers an update, perform a standard **in-place
   update**, without uninstalling, clearing app data, resetting the account,
   or changing the WSA instance. Obtain operator consent before the update
   if it interrupts a live game session.
4. Save redacted before/after version evidence and a normal-launch game
   screenshot. If an update succeeds, rerun read-only resource diagnostics
   and the readiness probe; the game's visible successful resource load,
   not the store update alone, is the required evidence.
5. If no supported update is offered or the official update fails, retain
   the existing data and record an explicit external/update blocker. Do not
   proceed to destructive acceptance.

## Safe diagnostic workflow

1. **Do not rerun destructive full acceptance immediately.** Each game-flow
   acceptance deliberately clears Kingshot app data and may remove local
   downloaded resources. Preserve the current user/game data and runtime report.
2. Inspect the exact-run `runtime-reports/<run-id>/` report, especially
   `mvp-full-acceptance.json`, `state.json`, `events.jsonl`,
   `tutorial-perception-failure.json` and its three screenshots.
3. Run a **non-destructive** probe in the same Windows user/session
   (this does not click, clear data or relaunch Kingshot):

   ```powershell
   cd C:\warbot_git
   & "C:\warbot_wsa\tugarin-venv\Scripts\python.exe" .\warbot_cli.py resource-diagnostics --backend wsa --serial 127.0.0.1:58526
   ```

   Inspect `debug/game-resource-network-diagnostics.json`:
   - `private_dns_mode`, proxy and VPN *presence indicators*;
   - whether Android connectivity mentions VALIDATED (not proof of game CDN);
   - **counts only** of selected process-scoped logcat network error classes.
   Raw Android logs, proxy addresses, DNS hostnames, credentials and request
   URLs are not persisted.
4. If the app visibly presents the same dialog when launched normally, confirm
   ordinary Android/WSA Internet and the game's *official* service status,
   then compare DNS/proxy/VPN configuration to the known working configuration
   and test a normal network path. Preserve a report of what was changed.
   Do not rotate identities or bypass security/captcha/account restrictions.
5. If diagnostics show DNS/TLS/connectivity problems, fix the legitimate
   host/network configuration and obtain one healthy in-game resource load
   **before** attempting another clean State #3/tutorial acceptance.
   If there is evidence of a genuine service outage, wait for service recovery.
   If status is unclear, use the in-game support option with sanitized evidence.
6. Only after an actual successful resource load, run a new single
   exact-SHA host acceptance with both GitHub CI runs green. Continue to
   fail closed if the dialog persists.

## Release rules and known limitations

- Resource-error clicks remain bounded at **two**, then terminal FAIL.
- No third click, no blind coordinates, no fallback to non-production transport.
- The exit reason is `GAME_RESOURCE_LOADING_FAILED`; this is an external
  resource-loading diagnostic category, *not* evidence of confirmed CDN outage.
- A new diagnostic report is *read-only* and cannot determine the game's
  private CDN endpoint without trusted service/network evidence.
- State #3, naming and two-character soak stay **UNPROVEN** until exact-host
  full acceptance overall=pass. Do not mark PR #6 ready, merge, or tag v1.0.0.
