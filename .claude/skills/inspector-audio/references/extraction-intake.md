# Extraction → bucket → inspector handoff

How audio + alignment artifacts go from a contributor's source links to a
reviewable `reciters/<slug>/` folder the inspector picks up. Two doors in:

- **ALIGN** — a slug already exists (catalogued delivery) and it is aligned,
  either by the native align pipeline (Requests-tab Align button,
  `docs/reference/align-pipeline.md`) or by offline Katana extraction.
- **INTAKE** — a slugless intake request (new combo / new reciter) that the
  owner plans, mints and aligns online from the Requests tab
  (`services/admin/intake_plan/`, align-pipeline.md § Online intake).

Both converge on the same reconciler: once `reciters/<slug>/` appears for a slug
in `AWAITING_ALIGNMENT`, `auto_detect` fires `reciter.alignment_completed` and
the row moves to `AWAITING_REVIEW`.

No audio/peaks/route logic here — that's `backend.md` / `peaks.md` / `prefetch.md`.

## The writers of `reciters/<slug>/`

Two writers produce per-reciter content: the native align pipeline
(`services/admin/align_pipeline/` + the `acquire_audio` / `split_audio` HF jobs,
see `docs/reference/align-pipeline.md`) and offline Katana extraction
(`.local/extraction/`). The Katana layout is below. For a given slug it fetches/probes source audio, runs VAD → CTC ASR →
DP alignment, and writes the full `reciters/<slug>/` set:

```
reciters/<slug>/
├── audio/<chapter>.mp3       # Xing TOC injected if VBR (audio_persist::_ensure_xing)
├── peaks/<chapter>.json.gz   # slim int8 packed gzip (schema v3)
├── detailed.json             # segments / timestamps / low_confidence / auto_split / pipeline_meta
├── segments.json
├── edit_history.jsonl
├── edit_history_peaks.jsonl
├── pipeline_meta.json
├── auto_split_v1.json
└── audio/_done.json          # written atomically LAST; offline audit/upload artifact only — NOT read by the inspector at runtime
```

Chapter keys: `"1"`..`"114"` for `by_surah`, `"<surah>:<ayah>"` for `by_ayah`.
The pipeline also writes the audio-manifest sidecar
`catalog/audio_manifest/<slug>.json` (per-chapter URL + bitrate + duration + size
+ mode) — single source of truth for chapter↔URL routing and VBR mode. See
`backend.md`.

The inspector at runtime only **reads** `reciters/<slug>/` — it never fetches
from a CDN to warm the bucket, and **nothing deletes it** (the hourly GC sweeper
was removed; content persists indefinitely). See `prefetch.md`.

### YouTube / yt-dlp sources *create* the encode (vs preserve it)

CDN sources keep the publisher's mp3 bytes verbatim and only get a Xing seek
header injected (`-c:a copy`). Playlist sources (YouTube, Google Drive,
SoundCloud, archive.org) are the exception. They arrive through the online
intake, and the align pipeline's acquire job (`qua_jobs/audio_io.py::encode`)
does ONE controlled encode → **192 kbps CBR / 44.1 kHz / source channel count**,
`-vn` (cover-art stripped — an APIC stream 0-byte-muxes on the static ffmpeg).
192k: `bestaudio` is opus ~130–160 kbps and opus is ~1.4× more bit-efficient than
mp3, so 192k CBR preserves it transparently. A file holding several chapters is
encoded once into a source slot `audio/<901+>.mp3`, aligned whole, then cut per
chapter by the `split_audio` job. The watch URL can't be HTTP-frame-probed, so
the mint writes a URL-only manifest and
`services/admin/align_pipeline/manifest.py` fills size / duration / bitrate /
source offset from the produced files. These deliveries are bucket-served only —
the watch URL is provenance, never streamed by the audio-proxy. See `catalog.md`
§5 and `docs/reference/align-pipeline.md`.

### Probing CDN stream-through sources (`qua_shared/mp3_probe.py`)

When authoring the audio-manifest sidecar from a CDN source rather than from
produced bucket files, probe **per-chapter, never per-delivery**. way2quran mixes
sample-rate and bitrate chapter-to-chapter *within a single delivery* (nabil-hatim:
77 chapters @ 44100 Hz + 37 @ 48000 Hz; abdul-salam-ramadan: 113 @ 48000 + 1 @
22050), so one chapter's metadata does not generalize. VBR is present (~18% of
deliveries), not absent.

Cloudflare ignores `Range` on a cold-cache MISS (returns `200` with the full body
instead of `206`). The `status == 200` re-slice branch in `mp3_probe._fetch`
(dropping the leading ID3 tag) is load-bearing for any new CDN source — verify it
survives when adding one.

## The reconciler — `services/segments/auto_detect.py`

A single-worker background loop (`start_background_loop`, default 60 s; gated by
`INSPECTOR_AUTO_DETECT=1`, surfaced via `/healthz`). Each pass diffs the set of
slug folders under the `reciters/` content prefix against a process-local "seen"
set, and for every **new** slug:

1. `state.get_row(slug)` — if the row is `None` or its state is not
   `AWAITING_ALIGNMENT`, skip (the slug is marked seen so it isn't retried).
2. Else `state.transition(slug, "reciter.alignment_completed", actor=SYSTEM_ACTOR)`
   → row moves `AWAITING_ALIGNMENT → AWAITING_REVIEW` ("Available for review").

`SYSTEM_ACTOR = Actor(hf_user_id="system", login_at_time="system", role=OWNER)`
— owner role is required because the same transition applies the pending
catalog edits via `pending_requests.apply_and_archive_completed`.

`hydrate_initial_seen()` runs at boot: it snapshots current slug folders into the
seen set **and** catch-up fires `alignment_completed` for any slug already in
`AWAITING_ALIGNMENT` with content on the bucket (covers an upload that completed
while the server was down). Idempotent.

> **Gate, not a folder scan for "done".** The reconciler keys on the *state row*,
> not on `_done.json` — and at runtime **nothing** consults `_done.json` (it's an
> offline audit/upload artifact only; the old TTL/sweeper that read it is gone).
> A slug whose folder appears **without** a state row in
> `AWAITING_ALIGNMENT` is silently ignored — it is marked seen and never fires.
> This is why intake content must seed `AWAITING_ALIGNMENT` (below) before the
> folder lands, not after.

## The three request kinds

All requests live in one `requests` table, discriminated by the `kind` column.
The state machine, audit log, and per-reciter content are identical across kinds
— they differ only in how the slug comes to exist.

| `kind` | Slug at submit | Path |
|---|---|---|
| `existing_combo_edit` | a real catalogued slug | Slug-based edit request. `reciter.requested` seeds `AWAITING_ALIGNMENT` + a pending entry; the align pipeline (or Katana extraction) aligns the slug; `auto_detect` flips it to `AWAITING_REVIEW`. End-to-end working — `routes/claims/requests.py::submit_request`. |
| `existing_reciter_new_combo` | `NULL` (slugless) | Reciter exists; the (riwayah, style) combo does not. The owner's plan → Align mints the delivery and starts its align run. |
| `new_reciter` | `NULL` (slugless) | Neither reciter nor delivery exists. The plan proposes the canonical `reciter_id`; plan → Align mints reciter + delivery and starts the run. |

Slugless intake submission shape (`qua_shared/schemas/wire/intake_requests.py`,
`routes/claims/requests.py::submit_intake` → `services/admin/intake.py::submit`):
the row carries `kind`, `reciter_id` (combo only), `proposed_edits`
(`ProposedEdits` — riwayah/style/identity/recording fields), `source`
(`IntakeSource`: direct per-chapter `links` or a `playlist` URL), and
`attestations` (distribution / links-verified / storage rights — all required).
Everything not a first-class column lands in the row's `payload` JSON.

**There is no accept step.** A slugless submission lands `pending` and is
directly alignable; the owner's decision to align it IS the acceptance. The
source, channel, slug and audio metadata are decided at plan / mint time, not
at submit, because they depend on what the source actually holds.

## Work queues

| Queue | Discovery | What runs |
|---|---|---|
| **ALIGN** | `delivery_states.state == 'awaiting_alignment'` | Slug already minted (any kind). The native align pipeline (Requests-tab Align) or Katana extraction writes `reciters/<slug>/`. |
| **INTAKE** | `requests` where `status='pending' AND slug IS NULL AND kind IN ('existing_reciter_new_combo','new_reciter')` | Shown in the Requests tab's Open facet with the intake plan panel. |

## Online intake: plan → mint → align

The offline `ingest_intake.py` driver, its LLM-reviewed playlist chapter map and
the bearer `POST /api/admin/intake/<rid>/ingest` route are **removed**. The flow
now runs from the Requests tab (routes `routes/admin/intake_plan.py`, gated by
`intake.ingest`; `/align` also needs `intake.align`):

- `GET` / `POST` / `PUT /api/admin/intake/<rid>/plan` — build (enumerate the
  source into files + propose identity, in a background thread; titles are never
  mapped to chapters — the align run detects each file's surahs),
  read, and save the owner's review of `payload.plan`.
- `POST /api/admin/intake/<rid>/align` — `intake_plan/mint.py` builds the
  `intake.ingest()` body from the plan, mints, then calls `align_runs.start`.

Full detail: `docs/reference/align-pipeline.md` § Online intake.

### The mint (`services/admin/intake.py::ingest`)

Still the one atomic mint, now called in-process by `mint.py`. The audio-manifest
sidecar is written **before** the DB transaction (a pre-write orphan is harmless
and overwritten on retry; a post-commit write could leave a delivery without a
manifest). Then one `durable_transaction()`:

1. Apply `vocab_additions` (idempotent `add_source` / `add_channel`).
2. `catalog.add_reciter(...)` when new (idempotent on `reciter_id`).
3. `catalog.add_delivery(Delivery(**delivery))`.
4. `state.transition(slug, "reciter.requested", ...)` — seeds
   `AWAITING_ALIGNMENT` + its pending entry.
5. `repo_requests.resolve_by_id(..., status='accepted', slug=slug)` — back-fills
   `requests.slug`.

Idempotent: a row that already has a `slug` returns it without re-minting. The
state row exists **before** the align run writes the folder, so the reconciler's
gate is satisfied.

The DB (`db/inspector.db`, synced to the bucket) is the sole source of truth for
state / catalog / requests. Bucket JSON under `requests/`, `state/`, `catalog/`
(other than the per-reciter manifest sidecar) are dead backups — never read or
write them.

## Where to look

| File | Role |
|---|---|
| `services/segments/auto_detect.py` | reconciler loop, `SYSTEM_ACTOR`, catch-up firing |
| `services/admin/intake.py` | slugless submit / probe / resolve / `ingest` (the mint) |
| `services/admin/intake_plan/` | online plan: enumerate (+ Drive), identity, plan lifecycle, mint → align (playlist files minted as manifest `sources`) |
| `routes/admin/intake_plan.py` | `/api/admin/intake/<rid>/{plan,align}` |
| `qua_shared/schemas/wire/intake_requests.py` | `IntakeSubmission`, `IntakeSource`, `IntakeAttestations` |
| `services/db/repo_requests.py` | `requests` table — `submit`, `resolve_by_id` (slug back-fill), `set_payload` |
| `services/state/catalog.py` | `add_reciter`, `add_delivery`, `add_source`, `add_channel` (the mint calls) |
| `qua_shared/schemas/bucket/catalog.py` | `Delivery`, `ReciterEntry`, `Source`, `Channel`, `Vocab`, `AudioManifestSidecar` |
| `qua_shared/schemas/config/state.py` | `ReciterState` (catalogued → … → released) |
| `routes/claims/requests.py` | submit / probe / return / discard routes |
