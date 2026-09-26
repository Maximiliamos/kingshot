# Development workflow

## Branches

- `main` — last verified baseline.
- `feature/tutorial` — extend new-character tutorial automation.
- `feature/state-selection` — make exact kingdom/state #3 selection reliable.

## Rule for changes

1. Keep `main` at a tested point.
2. Make one logical change per feature branch.
3. Test on the real HONOR Magic V3.
4. Record the observed result in `docs/PROGRESS.md`.
5. Merge only after the real-device test passes.

## Runtime evidence

When the bot reaches an unknown screen, attach or inspect:
- the latest PNG from `C:\warbot\unknown`;
- the relevant lines from `C:\warbot\logs\bot.log`.

Runtime evidence should not be committed to `main` by default.
