# Progress

## Confirmed working

- ADB connection to HONOR Magic V3.
- ADB tap inside the game.
- HONOR Suite fullscreen screen mirroring.
- Monitor capture with MSS.
- Phone-area crop for 1060×2376 aspect ratio.
- OpenCV template matching.
- Coordinate mapping from mirrored phone image to real phone.
- Recovery loop for MSS / BitBlt capture failures.
- F8 global emergency stop.
- Strict step-by-step navigation:
  - city
  - governor profile
  - settings
  - characters
  - create character
  - kingdom selector
  - confirmation dialog.
- First tutorial concept: use the task scroll and then the proposed Upgrade action.

## Latest real test

The bot successfully:
1. opened the governor profile;
2. opened Settings;
3. opened Characters;
4. started creating a new character;
5. entered `3` in kingdom search;
6. confirmed character creation;
7. reached the new-character tutorial.

### Known defect

The character was created in **state #23 instead of #3**.

Do not treat the current “first result after searching 3” assumption as valid. The exact selection logic must be fixed and verified on a real kingdom-results screen.

## Tutorial observations

New-character tutorial screens observed include:
- cinematic/dialog with **Пропустить**;
- dialogue requiring advance;
- loading screen;
- game world/tutorial;
- task scroll workflow;
- building screen with **Улучшить**.

The goal of the next phase is to automate the tutorial until the governor menu becomes available again.

## Next milestones

1. Exact state #3 selection.
2. Tutorial completion to governor-menu unlock.
3. Rename character to `Тугарин<N>`.
4. Create next character.
5. Persist nickname counter.
6. Controlled app-data reset and repeat.


## Tutorial branch update — 2026-09-26

Real-device evidence after character creation showed the actual order is:

1. game loading screen;
2. skippable intro/cinematic with `Пропустить`;
3. tutorial gameplay;
4. task-scroll / building-upgrade loop.

`feature/tutorial` now:
- migrates the old runtime step `tutorial_scroll` to `tutorial_intro`;
- waits through the real loading screen;
- detects the real `tutorial_skip.png` button before clicking;
- never blindly taps the top-right area;
- retries Skip only while the Skip template is still visible;
- then waits for the task scroll and Upgrade actions;
- treats the governor avatar becoming available as the completion signal for the mandatory tutorial.

Latest commits: `d2461ff`, `5c4b21b`, `219c376`.
