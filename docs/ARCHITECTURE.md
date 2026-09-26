# Architecture

## Data flow

```text
HONOR Magic V3
    │
    │ USB
    ▼
HONOR Suite fullscreen stream
    │
    │ Windows screen capture (MSS)
    ▼
Python bot
    │
    ├─ crop portrait phone image
    ├─ OpenCV template matching
    ├─ state machine / safety checks
    └─ coordinate conversion
         │
         ▼
ADB input tap / text / keyevent
         │
         ▼
HONOR Magic V3
```

## Why HONOR Suite is used for vision

The game returns a black image through standard Android `screencap` on this device. HONOR Suite can display the game correctly, so the bot captures the PC monitor instead.

## Important timing constraint

The HONOR Suite stream can lag the real phone by about **3–5 seconds**. The bot therefore blocks reactions for several seconds after an action so it does not repeatedly click an old frame.

## Safety model

The bot should prefer **doing nothing** over guessing.

Rules:
- only act on the expected state-specific UI;
- use contextual templates, not generic X/confirm buttons where possible;
- unexpected screens are saved under `unknown/`;
- F8 is the global emergency stop;
- `--dry-run` must not send input to the phone.

## State machine

Current main flow:

```text
create_character:
  home
  -> profile
  -> settings
  -> characters
  -> kingdom_picker
  -> kingdom_results
  -> state_confirm
  -> tutorial_new_character

tutorial_new_character:
  tutorial_scroll
  -> tutorial_building
  -> tutorial_wait_scroll
  -> ...
```

The tutorial branch is intentionally incomplete and should be extended from real captured screens.
