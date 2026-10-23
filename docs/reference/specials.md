# Specials — Basmala, Isti'adha and the other non-verse formulas

A recitation often opens a chapter with the Isti'adha and/or Basmala, and some close
Al-Fatiha with Amin or a chapter with Sadaqa. These are **specials**: timed, recited,
not verses. The align pipeline strips them from `detailed.json` (verse data stays verse
data) and records them per reciter in `reciters/<slug>/specials.json`. A release ships the
openers; the Timestamps tab times them into their own shard readings.

## The bucket file — `reciters/<slug>/specials.json`

Schema: `qua_shared/schemas/bucket/specials.py` (`SpecialsDoc`, pure `extra="forbid"`,
golden `qua_shared/schemas/bucket/fixtures/specials.golden.json`).

```
{schema_version: 1, generated_at, chapters: {"<ch>": [SpecialEntry, ...]}}   # time-ordered
SpecialEntry {type, position: pre|post, start_ms, end_ms, words: [[s,e],...], confidence,
              source, clipped, voice_z, voice_verdict, voice_confirmed}
```

- **Times** are chapter-audio ms, the same clock as `detailed.json` and the shards.
- **`type`**: `Isti'adha`, `Basmala` (the openers), `Amin`, `Sadaqa`, `Takbir`, `Tahmeed`, `Tasleem`.
- **`position`**: `pre` = before ayah 1, `post` = after the last ayah. A special inside a
  chapter (a repeated take) is not recorded.
- **`clipped`**: the recorded span ran into ayah 1 (a swallowed muqattaʿ) and was cut at
  ayah 1's start.
- **`source`** is provenance and never leaves the bucket: `align` (written by an align
  run), `fatiha_verse` (Al-Fatiha's 1:1 in an edition that numbers the Basmala — it stays
  a verse in `detailed.json` and is *also* listed here), and the backfill sources
  `pipeline_strip` / `user_delete` / `in_detailed` / `aligner_backfill`.
- **`words`**: per-word `[start_ms, end_ms]`, empty until a timing pass times the opener.
- **Voice** (Al-Fatiha's Basmala): `voice_z` + `voice_verdict` (`same` / `different` /
  `needs_confirmation`) from the aligner; `voice_confirmed` is a maintainer's call and
  wins. `SpecialEntry.same_voice` reads `voice_confirmed`, then the verdict.

Read/write via `services/storage/data_dir.py` (`read_specials_doc` / `write_specials_doc`);
`storage_paths.specials_path`. The bucket audit validates it (`bucket_audit._audit_specials`).

## Al-Fatiha 1:1 is always a verse

In Hafs and Shu'ba the Basmala is verse 1:1. It stays in `detailed.json`, in the release verse files and in the missing-verse check, whoever's voice it is. The voice is a flag: its `specials.json` entry (`source: fatiha_verse`) carries the aligner verdict and any maintainer confirm, and the release `specials.json` exposes it as `same_voice` so consumers decide how to handle it. Deleting 1:1 for being in another voice was the retired `basmala_amin` flow; `scripts/backfills/restore_fatiha_basmala.py` undoes those deletions.

## Writers

| Writer | What it records |
|---|---|
| Align run / offline promote — `services/segments/specials_doc.py::specials_from_run`, called by `promote_build.build_artifacts` | Each `delete_segment` strip event whose `matched_ref` is a special name: `pre` if it ends by ayah 1 (+300 ms), clipped if it runs into ayah 1, `post` if it starts at the last ayah's end; mid-chapter rows dropped. Plus Al-Fatiha's 1:1 Basmala on `hafs`/`shuba` with the aligner's `basmala_voice` verdict (`run_dir/basmala_voice.json`, staged by `stage_assemble._write_voice`; `unavailable` records none). |
| Timing — `services/timing/specials_timing.py` (via `aligner_timing.time_chapter`) | Fills `words` on each `pre` opener after a timing call (see below). |
| Voice confirm — `services/segments/specials.py::confirm_voice` | `voice_confirmed`, audited `specials.voice_confirmed`; a call that changes the answer stamps the reciter's current GH member stale (`catalog_edit`, `stamp_stale(..., tracks=("gh",))`) so the next cut ships it. HF does not ship specials. |
| Backfill (one-shot) | The prod audit harness `.local/specials_audit/` (`build_specials.py` → `out/<slug>.json`). |

## Voice verdict

The aligner (`include_basmala_voice: true`, sent by `align_pipeline/params.py`) scores an
isolated Al-Fatiha 1:1 against the same recording's 1:2–1:7 (MFCC/pitch/spectral/level
z): `same` below 1.7, `different` above 2.3, `needs_confirmation` between. A holder of
**`segments.confirm_basmala_voice`** (maintainer default) settles or overrules it:
`GET /api/seg/specials/<reciter>` (`SegSpecialsResponse`) and
`POST /api/seg/specials/<reciter>/voice` `{chapter, start_ms, same_voice}`
(`SegSpecialsVoiceRequest` → `OkAck`).

## Timing + shards

`aligner_timing.time_chapter` (by-surah audio only) prepends the chapter's `pre` openers
(not `fatiha_verse`) to the recording's segments as `{segment_uid, matched_ref: <type>,
confidence, time_start, time_end}` and sends `specials: true`. The uid is
`derive_uid(chapter, 1_000_000 + i, start_ms)`, clear of every real segment index. The
aligner times them and gives each opener its own shard reading:

- native + word-profile readings carry `special: "Isti'adha" | "Basmala"` and sit on part
  `0:1` (Isti'adha, words `0:1:1`–`0:1:5`) or `0:2` (Basmala, words `0:2:1`–`0:2:4`);
  `ts_shard.py` pins a special reading's parts to its ref and omits the key when absent;
  the word audit admits `0:[12]:N` only on a special reading.
- the returned stored times give each opener's words, written back to `specials.json`.
- a failed opener (`failed_segments[].special`) is logged and dropped — it never holds
  back the chapter's verse shards.

Chapter-0 parts are never verses: `timestamps_native.is_verse_ref` keeps them out of the
release's verse projection and `reference/timestamps._ayah_of` files them under no ayah.

## Frontend

- **Timestamps tab:** opener readings decode with their `special` flag (`lib/recitation-data/specials.ts`). They show as named filmstrip cells, animate in the mega cells, and appear in teleprompter word and letter mode. Peaks follow because the chapter comes from `_meta.chapter`. They are guarded out of verse navigation and reports. Detail is in [`frontend.md`](frontend.md#tabstimestamps).
- **Segments tab:** `components/BasmalaVoiceNotice.svelte` + `utils/specials/basmala-voice.ts`. On chapter 1, for a `segments.confirm_basmala_voice` holder whose Al-Fatiha Basmala has a verdict or a saved call, a compact notice plays the clip through the preview player. It offers Same / Different voice and POSTs to `/api/seg/specials/<reciter>/voice`. A saved call can be changed.

## Release

`qua_jobs/cut_release._build_specials_json` emits `specials.json` per recitation
(`ReleaseSpecialsDoc`, `qua_shared/specials.py::project_release`): per shipped chapter the
first `pre` Isti'adha and Basmala (`start_ms`, `end_ms`, `same_voice` when known,
`words: [[n, s, e]]`), plus `default_basmala` — an own-voice, unclipped Basmala closest to
the reciter's median duration. No `source`, confidence or voice score is released; a
Basmala in another voice still ships, flagged `same_voice: false`.

## Not here

- Matching the openers (wraparound opener scan, bounded lead-in skip, Amin at 1:7) is the
  aligner's: see the qua repo's `docs/reference/opener-specials.md`.
- The retired `basmala_amin` validation category is gone; the voice verdict replaced it.
