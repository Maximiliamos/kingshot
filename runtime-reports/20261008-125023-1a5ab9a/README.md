# Non-destructive foreground and resource-readiness check — 2026-10-08

## Checked revision

- Branch: `feature/unified-android-backend`
- Exact checked SHA: `1a5ab9a1dd5e7be45a2c12f06510ad264b81afb4`
- Local checkout was clean after a fast-forward and exactly matched `origin/feature/unified-android-backend`.
- Both Windows CI jobs for this SHA were `success`:
  - [run 37765609343 / job 113272402182](https://github.com/Maximiliamos/kingshot/actions/runs/37765609343/job/113272402182)
  - [run 37765602502 / job 113272378056](https://github.com/Maximiliamos/kingshot/actions/runs/37765602502/job/113272378056)

## Safety boundary

This was a non-destructive host check. No game data/cache was cleared, no application was removed or reinstalled, no account was changed, and destructive acceptance was not run. Only the Google Play foreground task was closed, then the installed target activity was brought forward:

`com.got.globalru/com.unity3d.player.MyMainPlayerActivity`

This is the installed Russian game `Война за трон`; no international Kingshot package was substituted.

## Exit codes

| Command | Exit code | Result |
| --- | ---: | --- |
| `warbot_cli.py resource-diagnostics --backend wsa --serial 127.0.0.1:58526` | `0` | Diagnostic completed, but `status=external_resource_loading_unresolved` |
| `scripts/verify_resource_readiness.ps1` | `51` | `status=resource_readiness_unconfirmed`; game UI was not positively verified |

## Foreground evidence

After the Play Store task was closed, Android reported the resumed activity as `com.got.globalru/com.unity3d.player.MyMainPlayerActivity`; no Google Play resumed activity remained. The capture was still incomplete/partially black, and the foreground gate deliberately did **not** accept it.

Both readiness samples were identical:

| Field | Observed value |
| --- | --- |
| `foreground_state` | `conflict` |
| `foreground_confirmed` | `false` |
| `window_focus_kind` | `unknown` |
| `resumed_activity_kind` | `game` |
| `panel_kind` | `tutorial_dialog` |
| `resource_error` | `false` |
| `block_reason` | `unverified_dialog` |
| `known_game_ui` | `false` |
| `ocr_available` | `true` |
| `role_count` | `0` |

## Resource-error answer

No: the exact message `Не удалось загрузить ресурсы` was **not confirmed while the foreground gate was confirmed active**. The gate never reached `foreground_confirmed=true`; it therefore safely refuses to assert either the error's absence or a playable game UI. An earlier diagnostic capture, before Google Play was fully removed from the visible surface, visually contained that resource-error dialog, but it is not used as proof for the confirmed-foreground question.

## Diagnostics

- Game process was present; proxy was `false`, VPN was `false`, private-DNS mode was `unknown`, and no guessed CDN endpoint was probed.
- All collected game logcat network-error counters were zero. This does not establish asset/CDN reachability.
- Readiness confirmation: `0/2`; the script correctly blocked later destructive game-flow and soak checks.

## Anonymized local artifact inventory

Raw screen images are not published because they may contain account/device information. They are retained only on the Windows host under `C:\warbot_git\debug`.

| Artifact | SHA-256 |
| --- | --- |
| `resource-readiness.json` | `AAC3B1050AC0F68141BB8DAFA6B760C87AC16E3776FCAE559FD977BAC0171D32` |
| `game-resource-network-diagnostics.json` | `5E18B519AAA3DC11ABE39B1B00AF257A0CD126D4273BCAD4A099803055A66C74` |
| `kingshot-foreground-1a5ab9a.png` | `15A4F53AF5731D82A1F0383080BA4A467BD160CF0AD0221195972FB700F049F4` |
| `kingshot-foreground-clean-1a5ab9a.png` | `C61C87FF3C035CABFA8A52045F2AF517A6279A3446CE2181F443B6F2D94B9C4D` |

## Conclusion for the next correction cycle

The new foreground protection is fail-closed as intended: Android activity identity says `game`, while window-focus evidence remains unknown and the production frame is not a valid, independently confirmed game foreground. Resource readiness remains blocked (`exit 51`). The next development cycle needs to diagnose why the Windows foreground/PrintWindow evidence conflicts after Google Play is closed; this check did not alter source code.
