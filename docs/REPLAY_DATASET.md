# Real Kingshot tutorial frame replay dataset

The release target requires actual labeled host frames, **not synthetic screenshots**
and not screenshot paths pointing to the mutable `runtime-reports` branch.
No frames were silently copied from published reports: the existing connector
cannot reliably fetch the binary PNG blobs. The corpus below is intentionally
empty and not release-ready.

## Sources to curate

Host evidence can be copied from the run-specific published directories,
for example:

- `runtime-reports/20261006-201545-160f84a/tutorial-perception-20261006_201514-normalized.png`
  — novel battle/reward screen.
- `runtime-reports/20261008-043702-d1f95ae/tutorial-perception-20261008_043637-normalized.png`
  — earlier tutorial error/recovery stage.
- `runtime-reports/20261008-045328-752a89c/tutorial-perception-20261008_045305-normalized.png`
  — confirmed resource-loading error dialog.

These are examples to **review and label**, not prevalidated fixtures or assumed
positive labels. Add at least four true positives (multiple gameplay panels,
including a known button bbox) and four negatives (resource-error, unclear OCR,
similar-looking non-game buttons, disabled or shifted controls).

Do not commit images containing personal account details, private tokens,
emails or private conversations. Crop/redact outside relevant UI and ensure
the edited frame is representative of the original visual geometry.

## Manifest contract

Copy reviewed PNGs into `tests/fixtures/tutorial_replay/`. Update
`manifest.json`, adding entries like this only after inspecting the frame:

```json
{
  "id": "reviewed-host-scene-001",
  "origin": "real-host",
  "kind": "positive",
  "image": "reviewed-host-scene-001.png",
  "sha256": "64_hexadecimal_sha256_of_exact_png",
  "panel": "battle_reward",
  "ocr": [
    {"text": "Получить", "normalized": "получить",
     "loc": [306, 681], "w": 59, "h": 19, "score": 95}
  ],
  "expected_roles": {"battle_reward_claim": [302, 670, 67, 38]}
}
```

The example coordinates are illustrative, **not a verified label**.
`loc`, width and height refer to the *normalized* screenshot pixel space.
Each negative has `kind: "negative"` and explicit `forbidden_roles`;
an unsafe semantic button found there is a regression. The replay requires
a SHA-256 for every image and rejects missing files and paths outside the
corpus directory. A positive case must match its expected panel, semantic
roles and bounding boxes (IoU ≥ 0.4).

Run from repository root:

```powershell
python -m unittest tests.test_vision_replay -v
python .\vision_replay.py
python .\vision_replay.py --require-real-coverage
```

The first two commands test replay machinery and whatever fixtures exist.
**Only the final strict command** verifies that the minimum real-frame coverage
is present. Hosted CI runs the ordinary manifest replay on every PR (including
strict failure for malformed/missing existing cases); the exact-host MVP
acceptance additionally runs the strict real-coverage mode. Until the corpus
exists, the strict command intentionally returns failure.

The corpus should be expanded with each new unknown scene; keep negative
examples especially for false-positive state-changing actions.
