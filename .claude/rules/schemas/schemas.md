---
paths:
  - "qua_shared/schemas/**/*.py"
  - "inspector/routes/**/*.py"
  - "inspector/services/storage/bucket_audit.py"
  - "scripts/diagnostics/validate_bucket.py"
  - "scripts/codegen/regen_fe_types.py"
---

# Schemas

Deep reference: `docs/reference/data-migrations.md`, `architecture.md`. These are the don't-break-this invariants only.

- **Three folders, by where the shape lives:** `bucket/` external JSON artefacts · `wire/` HTTP request/response · `config/` state+db blobs. Every wire shape is a model here and codegen'd; the FE keeps only genuinely FE-only types (view-models, peaks transport, ts-client projections, public-bucket vocabulary) in `lib/types/{view-models,peaks-transport,ts-client,public-bucket}.ts`.
- **Routes serialize through wire models:** `Model.model_validate(...)` on the way in, `model.model_dump(mode="json")` on the way out. Errors/acks use `ErrorEnvelope`/`OkAck` — never inline dicts. **Exception — large uncached list responses** (e.g. `seg_all`, tens of thousands of segments): the per-request `validate`+`dump` round-trip costs hundreds of ms, so the route builds the exact wire dict and serves it raw. Allowed only when the wire shape is frozen by a response snapshot **and** conformance to the model is asserted at the test boundary (`test_wire_seg_models`), never on the hot path.
- **Touch any model → regen → commit:** `python scripts/codegen/regen_fe_types.py` then commit `inspector/frontend/src/lib/types/generated/schemas.ts`. New FE-referenced shape goes in `fe_types.py` first. CI `schema-codegen-check` gates via `git diff --exit-code`.
- **`extra="forbid"` is the default everywhere — including `bucket/` artefacts (pure forbid; an unknown/legacy field raises `ValidationError`).** `extra="allow"` only when a forward-compat fixture proves an unknown field rides through unchanged (the open provenance metas `ts_validation`, `ts_shard`'s `_meta`).
- **Validate external bucket files at write/audit time, never on cached reads:** `scripts/diagnostics/validate_bucket.py` (manual/CI) + nightly `bucket-validate.yml` + `/healthz?deep=1` sample. Don't re-validate bytes already served from cache.
