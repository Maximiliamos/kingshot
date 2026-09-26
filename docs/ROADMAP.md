# Automation roadmap

## Implemented in code
- Real HONOR device control through ADB.
- HONOR Suite screen capture with fail-safe pause.
- Persistent state.json.
- Context-based create-character state machine.
- Exact-state selection guard: state #3 must be visually identified; no first-row assumption.
- State #3 confirmation guard.
- Tutorial loading / Skip / dialogue / scroll / Upgrade states.
- Governor avatar as tutorial completion signal.
- Template validation/cache at startup.
- Unknown-screen fail closed.
- BitBlt recovery and F8 emergency stop.

## Requires real-device evidence before enabling
- Exact state #3 row template.
- Tutorial dialogue continuation template.
- Any additional mandatory tutorial screens.
- Rename UI and Unicode input.
- Character-per-cycle limit.
- Android package/activity verification before clear/relaunch.
- Full end-to-end soak test.

## Safety invariant
Unknown screens and server-side restrictions stop or wait. The bot must not guess clicks or attempt to bypass game restrictions.
