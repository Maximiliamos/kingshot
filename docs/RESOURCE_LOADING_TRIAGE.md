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

## App identity and version source of truth

The host targets Android package `com.got.globalru`, which the official
[Google Play listing](https://play.google.com/store/apps/details?id=com.got.globalru)
identifies as **«Война за трон» by Echofun Interactive Limited**. It must not
be confused with the different [Kingshot Google Play app](https://play.google.com/store/apps/details?id=com.run.tower.defense)
published by Century Games at package `com.run.tower.defense`.

The Android package `com.got.globalru` reports `versionName=1.12.10` and
`versionCode=163`, the same version shown in available public catalog
listings as of 2026-10-08. The official Play Store on the WSA host did **not**
offer an update. This does not establish the globally latest version or
explain the resource failure; it removes the assumption that the installed
package is definitely obsolete. Never switch packages or game accounts
during diagnostics without a separate product decision.

## Latest host evidence and focus isolation (2026-10-08)

[Host report `20261008-133320-4c0d34e`](https://github.com/Maximiliamos/kingshot/blob/runtime-reports/runtime-reports/20261008-133320-4c0d34e/README.md)
confirmed installed package `com.got.globalru` at `versionName=1.12.10`,
`versionCode=163` and in-game `1.12.10.1`. The official Google Play
listing for that device/account showed **Play / Uninstall** but **no Update**.
Do not conclude that all regions/devices run this same release.

The game resource-error dialog was visibly present before the store check.
After returning from Play Store, the captured desktop frame could still
include both game and external-store UI. Therefore a running game process
is **not** evidence of a clean Kingshot foreground.

The read-only `game_foreground.py` gate classifies Android window focus and
top resumed activity as `game`, `other`, `conflict` or `unknown`. It
stores **only these categories**, never foreground package names, raw
window titles or Android dumps, and requires positive game foreground
confirmation alongside safe game UI frames. A stale Play Store overlay,
ambiguous/unsupported Android focus output or missing OCR blocks readiness.
It does not assert that visible desktop pixels are unoccluded: the
independent image-based gate must still verify a safe game panel.

Local agent after green exact-head CI:

1. Preserve the installed game data, cache, WSA and account. Close/leave
   Play Store through **normal manual user interaction**, without any bot
   keyevents or unverified Android input.
2. Observe a clean dedicated Kingshot window without a Play Store overlay.
   Run the read-only resource diagnostic and readiness script on the exact
   integration SHA; preserve `debug/resource-readiness.json`.
3. Report `foreground_state`, `window_focus_kind`,
   `resumed_activity_kind`, `foreground_probe_errors`, `panel_kind`
   and `block_reason` for each of the two frames. Never publish raw
   `dumpsys`, logcat, game image or another app's package name.
4. If game is confirmed foreground but the loading error persists, mark
   external resource-loading cause **unresolved**. Distinguish verified
   network errors from zero counted messages, and consider official
   game support with privately submitted, redacted evidence. No data reset,
   APK sideload or destructive game-flow acceptance.

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
