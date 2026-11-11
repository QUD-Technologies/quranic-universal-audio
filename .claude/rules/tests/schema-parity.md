---
paths:
  - "qua_shared/schemas/**/*.py"
  - "qua_shared/tests/**/*.py"
  - "inspector/services/segments/save.py"
  - "inspector/services/audio/peaks_history.py"
  - "inspector/services/admin/intake.py"
  - "inspector/tests/persistence/**/*.py"
  - "inspector/frontend/src/lib/types/generated/**"
  - "scripts/codegen/regen_fe_types.py"
---

# Tests — schema parity + codegen

- Per-reciter artefacts MUST round-trip through `qua_shared/schemas/`: `DetailedSegment`, `EditHistoryBatch` + `EditOperation`, `PeaksRecord`, `AudioManifestSidecar`. Round-trip = parse → re-serialize → byte-equal to canonical fixture.
- Writer changes land in the same change as the round-trip test. No "we'll add the test later".
- After ANY edit to `qua_shared/schemas/*.py`, run `python scripts/codegen/regen_fe_types.py` and commit `inspector/frontend/src/lib/types/generated/schemas.ts`. CI's `schema-codegen-check` enforces this with `git diff --exit-code`.
- `qua_shared/schemas/fe_types.py` is the slim FE-facing re-export bridge — when a new schema is referenced by the FE, add it to `fe_types.py` BEFORE regen.
- Bucket artefacts (`DetailedSegment`, `EditHistoryBatch`, `PeaksRecord`, `AudioManifestSidecar`, …) are pure `ConfigDict(extra="forbid")`: an unknown/legacy field MUST raise `ValidationError`, not be stripped. Round-trip fixtures are clean — no stray keys.
- `ConfigDict(extra="allow")` survives only on the documented forward-compat exceptions (`ts_validation`, `ts_shard`'s `_meta`, `automation`); there consumers must NOT strip unknown fields, so their round-trip tests include a forward-compat fixture that carries an extra field through unchanged.
- BE↔FE constant parity (codes, blocking-count keys, registry order) needs an explicit parity test. Hand-rolled mirrors drift silently.

## Anti-patterns

- Hand-rolling a TS interface (`export interface PendingRequest { ... }`) for a shape that already exists in `qua_shared/schemas/` → add to `fe_types.py` and consume `schemas.ts`.
- Editing `qua_shared/schemas/*.py` without running `regen_fe_types.py` → CI fails on the next push; fix locally first.
- A new artefact type without a corresponding round-trip test → writer/reader drift is now invisible.
- Loosening a bucket artefact back to `extra="allow"` (or re-adding a strip/tolerate pre-validator) to "be safe" against legacy data → the prod data is clean and the artefacts are pure forbid; reach for `extra="allow"` only for a genuine forward-compat field that ships its own round-trip fixture.
- Vendored copies of canonical constants in `qua_shared/peaks_compute.py` etc. without a parity test → drift silently (e.g. `_FFMPEG_TIMEOUT=600` vs `FFMPEG_FULL_TIMEOUT=300`).
