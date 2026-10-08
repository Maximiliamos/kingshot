# TUGARIN BOTS: audit of flashing ADB consoles and runtime architecture

Date: 2026-09-30  
Branch: `feature/unified-android-backend`  
Scope: Windows process creation, scheduled tasks, dedicated-user boundary, WSA/ADB lifecycle, GUI polling, P0 evidence and report publication.

## Executive result

The repeated windows were not malware. They were console windows created by an old, long-running GUI instance under `COMPUTER\TugarinBots`. The dedicated-user continuation workflow automatically launched that GUI after P0. Its framebuffer and health timers periodically executed `adb.exe`; builds started before the `CREATE_NO_WINDOW` repair inherited visible console behavior. Two stale `pythonw.exe` processes kept the old code loaded even after the repository was updated.

Observed termination evidence on the host:

- stale dedicated-user processes: `adb:18292`, `pythonw:46448`, `pythonw:46936`;
- all `TUGARIN BOTS*` scheduled tasks were stopped and disabled;
- the post-stop process audit found no `adb`, `python`, `pythonw`, `cmd`, `powershell` or `conhost` owned by `TugarinBots`.

## Root causes

1. **Automatic GUI launch after P0.** `setup_dedicated_wsa_user.ps1` launched the desktop shortcut immediately after `WSA_GAME_PASS`. This left an unattended polling GUI in the disconnected dedicated-user session.
2. **Stale in-memory code.** Updating Git did not update already running Python processes. Old GUI instances continued using the pre-fix ADB launch behavior.
3. **Console launcher.** `run_gui.bat` used `python gui.py`, keeping a CMD console attached for the lifetime of the GUI.
4. **Incomplete early suppression.** The first repair covered `device_backend.py`, but installer property probes and diagnostic Python launches required separate hidden-process handling. These were subsequently moved to `Start-Process -WindowStyle Hidden` and the dedicated venv.
5. **Cross-session observability.** A GUI in the disconnected `TugarinBots` session was not visible from `Программист1`, while its child console windows became disruptive when the user switched sessions.

## Repairs now enforced

- all production ADB subprocesses in `device_backend.py` use `CREATE_NO_WINDOW`;
- installer ADB and Python probes use `-WindowStyle Hidden` with redirected output;
- scheduled migration and continuation tasks are hidden and non-interactive;
- dedicated-user setup creates the GUI shortcut but never auto-launches the GUI;
- GUI has a single-instance `QLockFile` guard;
- GUI and all its Python children normalize `python.exe` to sibling `pythonw.exe`;
- `run_gui.vbs` is the primary consoleless launcher; `run_gui.bat` is compatibility-only;
- the emergency stop script stops/disables every `TUGARIN BOTS*` task and terminates only console/probe processes owned by `COMPUTER\TugarinBots`.

## Architecture assessment

### Passed

- Registration and runtime use the same Windows SID (`TugarinBots`).
- GApps WSA AppX registration, Android 13 boot, authorized ADB, framebuffer, package manager, storage, network, Internet and audio gates passed.
- All three Kingshot APK splits installed.
- Kingshot PID `13812` passed the 120-second stability gate.
- Final and startup frames were not classified as the loading logo.
- Local pointer reported `WSA_GAME_PASS` for `wsa-p0-20260930-023938-45620`.
- GitHub runtime pointer advanced to report `20260930-024350-b565c8d`.
- Google Play Services, GSF and Play Store were present; the user subsequently confirmed successful Google authorization and entry into the game.

### Residual risks

- `adb.exe` normally maintains one background server process while the GUI is open. This is expected; it must be hidden, not repeatedly respawned visibly.
- A process started before an update retains old code. Operational upgrades must stop old GUI instances before launching the new version.
- A `.bat` file can briefly allocate a console when double-clicked. Use the VBS launcher or the desktop shortcut for a zero-console start.
- Google credential UI is protected and cannot be captured through ADB; password, OTP and CAPTCHA remain manual steps.
- Native ARM64 QEMU remains an experimental fallback and is not part of the accepted WSA runtime.

## Operational rule

Only one GUI may run, and it must be started deliberately through `run_gui.vbs` or the desktop shortcut. P0/reporting tasks must never launch the GUI. Before diagnosing any future flashing window, record process owner, session ID, parent PID and command line; do not use blind retries.
