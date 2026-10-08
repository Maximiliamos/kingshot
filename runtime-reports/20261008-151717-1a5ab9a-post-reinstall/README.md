# Post-reinstall Kingshot resource check — 2026-10-08

## Revision and scope

- Branch: `feature/unified-android-backend`
- Exact checked SHA: `1a5ab9a1dd5e7be45a2c12f06510ad264b81afb4`
- Source checkout was clean; no source files were changed.
- Target application: official Google Play package `com.got.globalru` (`Война за трон`, Echofun Interactive Limited).
- Installed version after the owner-authorized reinstall: `versionName=1.12.10`, `versionCode=163`.
- Notification decision: denied. Android evidence: `POST_NOTIFICATIONS granted=false`; AppOps mode `ignore`.
- This check did not clear data, reinstall again, press the resource retry button, or run destructive acceptance.

## Observed game result

After the notification prompt disappeared, the game completed its normal startup and displayed a real city interface. No system permission dialog or Google Play surface remained in the observed game image. The exact message `Не удалось загрузить ресурсы` did not reappear during this verification interval.

Android activity evidence showed:

`com.got.globalru/com.unity3d.player.MyMainPlayerActivity`

as the game resumed activity. The separate Microsoft WSA home activity remained listed on its own display/task, which is expected for WSA.

## Exit codes

| Check | Exit code | Result |
| --- | ---: | --- |
| `warbot_cli.py resource-diagnostics --backend wsa --serial 127.0.0.1:58526` | `0` | Diagnostic collection completed |
| `scripts/verify_resource_readiness.ps1` | `51` | `resource_readiness_unconfirmed` |

`resource-diagnostics` retained the conservative status `external_resource_loading_unresolved`, because it does not guess or directly probe a game CDN. All collected network-error counters, including `resource_download`, were `0` after reinstall.

## Two readiness samples

| Field | Sample 1 | Sample 2 |
| --- | --- | --- |
| `frame_valid` | `true` | `true` |
| `known_game_ui` | `false` | `true` |
| `resource_error` | `false` | `false` |
| `account_restriction` | `false` | `false` |
| `ocr_available` | `true` | `true` |
| `panel_kind` | `city_home` | `city_home` |
| `block_reason` | `game_foreground_unconfirmed` | `game_foreground_unconfirmed` |
| `role_count` | `0` | `0` |
| `tutorial_target_confirmed` | `false` | `true` |
| `foreground_state` | `conflict` | `conflict` |
| `foreground_confirmed` | `false` | `false` |
| `window_focus_kind` | `unknown` | `unknown` |
| `resumed_activity_kind` | `game` | `game` |

Readiness confirmation was `0/2`, so the gate correctly remained fail-closed. The failure is no longer evidence of a resource-error dialog: it is specifically a Windows/WSA foreground-evidence conflict.

## Anonymized evidence inventory

Raw images are retained only on the local Windows host under `C:\warbot_git\debug`; they are not committed because they may contain account or game-state details.

| Artifact | SHA-256 |
| --- | --- |
| `post-reinstall-readiness-frame-1.png` | `3AA92C677575312AA56C5358E73B23C7ED656EF332A931D11FE482553E4B5E0C` |
| `post-reinstall-readiness-frame-2.png` | `71847316F6210090025E760B1979BDC0B2A2270DDCADBD71A2429E2015C027BC` |
| `resource-readiness.json` | `D19D032CA6A407D6FCBAEE3D17264EA9588AF8F08843F40E8E220A30384D9FD1` |
| `game-resource-network-diagnostics.json` | `A2C72E386A259C077005BDA8E74D8EC80F67F4280F481C3BB4A321A4DE7B276B` |

## Developer conclusion

The previous resource-loading failure did not recur after the owner-authorized clean reinstall: a real `city_home` screen was observed and resource-download error counters were zero. The remaining reproducible blocker is the foreground classifier: Android confirms the game activity, while Windows focus evidence remains `unknown`, producing `foreground_state=conflict` and readiness exit `51`. No later gameplay or destructive acceptance was attempted.
