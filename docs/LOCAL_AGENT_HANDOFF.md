# Local-agent handoff — final MVP host acceptance

Use this only after hosted CI for `feature/unified-android-backend` is green.

## One command

Open PowerShell in the normal interactive `Программист1` session:

```powershell
cd C:\warbot_git
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\scripts\run_full_mvp_and_report.ps1" -ExpectedCommit <RELEASE_SHA>
```

Do not edit tracked files before the run. The runner requires the local HEAD to
match the pinned release SHA exactly. It will attempt a fast-forward pull first,
but a transient GitHub/TLS failure no longer blocks validation when local HEAD
already equals -ExpectedCommit. Any SHA mismatch still fails closed.

## What it will do

Fail-fast sequence:

```text
WSA/GApps/Kingshot infrastructure
→ Google Services
→ PrintWindow preview >=15 FPS
→ consoleless GUI render
→ operator Unicode/UI/audio
→ Kingshot restart + ADB reconnect
→ initial tutorial
→ exact State #3
→ new-character tutorial
→ Тугарин<N> + screenshot/OCR evidence
→ clean reset
→ next Тугарин<N+1>
→ minimum two-character soak
→ final process + migration-task audit
```

The game-flow/soak stages intentionally run `pm clear` on Kingshot. The
PC-side nickname counter is preserved.

## Human-only actions

Only intervene for genuine OS/account authorization that cannot be automated
safely (for example UAC, Google password/2FA, or an Android trust prompt).
Do not manually click through an unknown Kingshot tutorial screen just to make
the run pass.

If the bot stops on an unknown screen/account restriction, leave the evidence
intact. The failed run is still uploaded and is the input for the next code
patch.

## Result

The script uploads one report folder to the `runtime-reports` branch. Its
manifest contains size + SHA-256 for every collected evidence file. The
critical acceptance file is:

```text
mvp-full-acceptance.json
```

Release success requires:

```json
{
  "overall": "pass"
}
```

on the exact published commit.

After that evidence is verified remotely, and only then:

```text
PR #6 Draft → Ready
→ merge main
→ main CI green
→ tag v1.0.0
```
