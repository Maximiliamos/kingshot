# Full-cycle design

Target high-level state machine:

CREATE_CHARACTER -> TUTORIAL -> RENAME -> CHECK_LIMIT -> CREATE_CHARACTER

When the configured legitimate character limit is reached:

SAVE_PC_STATE -> CLEAR_APP_DATA -> RELAUNCH -> INITIAL_TUTORIAL -> CREATE_CHARACTER

## Persistence
PC-side state keeps:
- next_nickname
- characters_created
- current_cycle
- phase / step
- target_state

Counters are advanced only after the corresponding action is confirmed on-screen.

## Rename
Cyrillic nickname input must be implemented only after the rename UI is captured and a Unicode-capable input path is verified on the real device. Plain `adb shell input text` is not assumed to support Cyrillic.

## Reset/relaunch
The package name and launcher activity must be discovered from the connected device before any `pm clear` implementation is enabled. No package name is hard-coded from web guesses.

## Recovery
- lost HONOR fullscreen: pause;
- ADB unavailable: stop/wait;
- BitBlt failure: recreate capture;
- unknown UI: save screenshot, no blind tap;
- server limit / anti-bot / account warning: stop and require human review.
