# Template assets

The runtime bot expects PNG templates in `C:\warbot\templates`.

Current v4 template names:

- `confirm_button.png`
- `create_plus.png`
- `governor_avatar.png`
- `invasion_title.png`
- `newbie_offer_context.png`
- `offline_confirm.png`
- `profile_settings.png`
- `select_kingdom_title.png`
- `settings_characters.png`
- `state3_modal.png`
- `task_scroll.png`
- `upgrade_button.png`

These templates were produced from real HONOR Suite captures at the current phone/stream geometry.

## Important

Do not replace contextual templates with generic icons such as a bare `X` button. A previous version confused unrelated screens and opened Language / Events / Store.

The binary PNG assets currently remain on the development PC under `C:\warbot\templates`. They should be committed from the local checkout so a fresh clone becomes fully self-contained.
