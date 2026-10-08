# Kingshot official Play update check — 2026-10-08

## Scope

- Branch: `feature/unified-android-backend`
- Verified source SHA: `4c0d34e151e201908803934dde34d7ba7b1999c0`
- Local checkout: clean and equal to `origin/feature/unified-android-backend`.
- Windows CI for that exact SHA: both `windows-poc` jobs passed.
  - [run 37763353087 / job 113264901891](https://github.com/Maximiliamos/kingshot/actions/runs/37763353087/job/113264901891)
  - [run 37763347762 / job 113264884958](https://github.com/Maximiliamos/kingshot/actions/runs/37763347762/job/113264884958)

## Authorization and safety boundary

The owner authorized an official Google Play update over the existing game only. No Kingshot data or cache was cleared; the application was not removed; WSA was not reset; the account and security settings were not changed; no third-party APK was used. Destructive acceptance was not run.

## Versions and Google Play result

| Item | Result |
| --- | --- |
| Package | `com.got.globalru` |
| Installed before check | `versionName=1.12.10`, `versionCode=163` |
| In-game version seen before check | `1.12.10.1` |
| Official store | Google Play listing for `Война за трон`, publisher `Echofun Interactive Limited` |
| Store-offered version | Not exposed: listing showed `Играть` and `Удалить`, not `Обновить` |
| Update attempt | Not performed: no official update was available |
| Installed after check | `versionName=1.12.10`, `versionCode=163` |

## Runtime results

- WSA ADB state: `device`; display awake and on; Kingshot process was present.
- Before opening Google Play, the normal Kingshot capture showed `Не удалось загрузить ресурсы!` with the two bounded-dialog actions. No dialog button was pressed.
- Returning from Google Play did not establish a clean standalone game foreground capture: the production capture still contained the resource-dialog area together with an Android/Google Play foreground overlay. This composite capture is not accepted as proof of a real game UI or of dialog recovery.
- `resource-diagnostics` exited `0`, but reported `external_resource_loading_unresolved`. Its observed game-network counters were zero; proxy was `false`, VPN was `false`, and private-DNS host mode was not configured/unknown.
- `verify_resource_readiness.ps1` exited `51` with `resource_readiness_unconfirmed`. Both samples had a valid frame and OCR available, but `known_game_ui=false`, `role_count=0`, and `tutorial_target_confirmed=false`; confirmation was `0/2`.

## Anonymized local diagnostic inventory

Raw captures are deliberately not published because they can contain account or device information. They remain on the Windows host under `C:\warbot_git\debug` and can be matched by SHA-256:

| Local material | SHA-256 |
| --- | --- |
| `kingshot-preupdate.png` | `9D66EA254D678210ACF2990511F6E8F8182DC212F46A2B6BF22BF38FF1C9CEB5` |
| `play-kingshot-preupdate.png` | `DBE7E06D7DAE0F71CAEF8A020619A063D501F189A64E2E17B8F344C2256A3BC4` |
| `kingshot-post-play-check.png` | `B08A0FF1745CBFE366A7F8549BF32E06843BEA731469441403AB78225E075AB7` |
| `kingshot-post-play-foreground.png` | `00E422B3224E9262D36F1B9943E520D822A13AE355B5D522E2D9F1069740B59F` |
| `resource-readiness.json` | `CE9FDCABC6353216C1A746822F216CC4B91C0817D3C24006CA997E156D4C6CD8` |
| `game-resource-network-diagnostics.json` | `08E008434393B0D4D40D2E43245E663CE83764E2920BC8835ED0C16B9E556046` |

## Blocking conclusion

MVP progression remains blocked. Google Play offered no update, the installed version stayed unchanged, and Resource Readiness safely failed closed (`exit 51`) instead of recognizing a playable interface. The resource-loading problem is still observed and there is no valid evidence of a real loaded game UI. No source-code change was made in this verification run.
