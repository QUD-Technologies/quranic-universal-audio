# Editions (multi-riwayah)

Universal Audio supports four riwayat end to end — ingestion, review, timing,
publishing. This is the map of what that means, what changes per edition, and
what deliberately does not.

Open this when you touch anything that renders Quranic text, resolves a verse or
word coordinate, or decides what a delivery's timings contain.

---

## 1. The four editions

| Inspector slug (`riwayahs.slug`, the wire) | SDK slug (`qua_domain`) | Words | Ayahs |
|---|---|---|---|
| `hafs_an_asim` | `hafs` | 77,433 | 6,236 |
| `warsh_an_nafi` | `warsh` | 77,428 | 6,214 |
| `qalon_an_nafi` | `qalun` | 77,428 | 6,214 |
| `shubah_an_asim` | `shuba` | 77,433 | 6,236 |

**Warsh and Qalun renumber 50 of the 114 surahs.** Al-Baqarah ends at 285, not
286. `2:1` there is eight words (Hafs `2:1` + `2:2` merged), and the Fatiha
Basmala is an unnumbered opener rather than verse `1:1`. Any code that assumes
Hafs geometry produces confident, wrong answers for these two.

### Three slug vocabularies, and which one you are holding

1. **Inspector** — `hafs_an_asim`. The `riwayahs.slug` column, the catalog, and
   every HTTP boundary (`?riwayah=`).
2. **SDK** — `hafs`. What `qua_domain` and the shards speak.
3. **`short`** — the vocab display abbreviation. **Nothing keys on this.**

`qua_shared/riwayat.py` is the only mapping table (`SUPPORTED_RIWAYAT`), mirrored
to the frontend in `lib/riwayat.ts` and typed off the codegen'd union, so adding
a fifth riwayah backend-side breaks `npm run check` rather than shipping a stale
list. Use `to_sdk_slug` / `from_sdk_slug` at a boundary (strict, raises) and
`resolve_sdk_slug` only when reading stored state that may carry either form.

---

## 2. The dependency: `qua-domain`

The Inspector takes a real pip dependency on `qua-domain`, installed from a
pinned `Hetchy/qua` commit — the same package and the same mechanism the aligner
app uses, so the two cannot disagree about what a Warsh word is.

| Where | How |
|---|---|
| Docker | build stage `qua-domain-build`, `--mount=type=secret,id=QUA_DOMAIN_DEPLOY_KEY`, sparse blobless fetch of `packages/quran-domain`, `pip wheel` |
| Local dev / CI | `python scripts/devenv/install_qua_domain.py` |
| No credential | exits 0 having installed **nothing** — a Hafs-only runtime |

`services/reference/editions.py` is the **only** module in the Inspector that
imports `qua_domain`. Everything else goes through it.

### Hafs is refused there, on purpose

`editions._require_non_hafs()` raises `HafsNotRoutedHere` for Hafs text and font.
`qua_domain`'s Hafs index has the same 77,433 refs but different glyph spellings
in **44,481** words (U+0652 vs U+06E1 and friends). Swapping the Hafs display
path to it would silently change every existing render, snapshot and qalqala
derivation. Hafs display text stays on `data/qpc_hafs.json` +
`data/digital_khatt_v2_script.json` + `DigitalKhattV2.otf`.

Hafs *counting* is a different matter: `surah_info.json` and
`load_edition_index("hafs")` are byte-identical (6,236 verses, 77,433 words, zero
diffs) and a parity test keeps them so. The Hafs runtime path therefore keeps
reading `surah_info.json` and works with the package absent.

### Degradation

`INSPECTOR_MULTI_RIWAYAH=0` (or an unimportable `qua_domain`) forces a Hafs-only
runtime. A non-Hafs delivery then **fails loudly** — `EditionsUnavailable`, a
400, a 503, or an omitted surface. **No path anywhere falls back to Hafs for a
non-Hafs riwayah.** Rendering one edition's coordinates under another's script
produces plausible-looking text with wrong refs, which a reviewer would then
save.

`/healthz` reports the installed editions.

---

## 3. What is projected, and what is not

Recognition and DP matching always run against **Hafs**; MFA phonemizes **Hafs
proxy phones**. The result is *projected* onto the delivery's edition. So:

| Fact | Source |
|---|---|
| Coordinates a reviewer sees and edits (`matched_ref`) | the delivery's edition |
| The Hafs span the matcher actually matched (`source_ref`) | Hafs, recorded on the seg |
| `projection_support` | `full` / `partial` — `partial` means a source word in the span reached no target word, or sat in an N:M relation the projection could only partly carry |
| Display text + font | the delivery's edition |
| Verse word counts, ayah counts, stop signs | the delivery's edition |

Both provenance fields are **derived, never sent by the client**: the editor
works in the delivery edition's coordinates and knows nothing about Hafs.
`services/segments/stamping.py` re-derives them from `matched_ref` on every
write — save (`save.py`) *and* undo (`undo._restamp`), because the frontend's
before-snapshot carries neither field and restoring it verbatim would drop
them. Inheriting the old value would align the wrong audio; dropping it would
make the timestamps engine refuse the delivery, which it does —
`assert_projected_segments_are_sourced` runs before any alignment. Support is
computed from the SOURCE side by `qua_shared/projection_support.py`.

`source_ref` is always the **whole projection group** `matched_ref` closes over:
Warsh writes Hafs `40:26:13`+`14` as one word, and a segment ending on it hands
MFA both Hafs words or gets half the phones of the word it is timing. The staged
run's own `source_ref` can be narrower — there it is evidence, the text the
matcher actually had — and promote widens it. The two records disagree by design;
`qua_shared/tests/test_staged_run.py` and `tests/services/test_projection_stamp.py`
pin the two sides.

A `matched_ref` that names no word of the delivery's edition raises
`RefNotInEdition` → **400 `REF_NOT_IN_EDITION`**. `reverse_range` never returns
an empty span; it raises, and with two unrelated types (a malformed ref is
`InvalidQuranReferenceError`, an out-of-edition one a bare `KeyError`), so both
are caught at the stamp and reported as the client error they are.

### Verse numbering does not line up

**Warsh and Qalun renumber 50 of the 114 surahs**, and that is the fact most of
this document rests on. It is not only an offset — verses merge and split:

| | warsh / qalun |
|---|---|
| Source verses whose words reach **two** target verses | 59 |
| Target verses fed by **two** source verses | 77 |
| Ayah count | 6214 (Hafs and Shu'bah: 6236) |

Shu'bah renumbers nothing — it is Hafs's numbering with a different word text.

Two consequences worth stating outright:

- **One occurrence, one verse — in the coordinates it is *aligned* in.** Warsh
  `2:1` is a single Warsh verse and two Hafs verses, so it looks clean in
  `matched_ref` and breaks the shard builder. The timestamps runner's pre-flight
  guard therefore tests `build_mfa_ref(seg)` (i.e. `source_ref`), not
  `matched_ref`; such a segment has to be split before it can be timed.

  > **Known limitation.** Auto-Split cannot yet offer a cut for these 77 verses
  > per edition: it dispatches on `matched_ref`, which is single-verse here, so
  > `cross_verse_sections` answers `None`. The remedy is a manual split in the
  > Segments editor at the word where the Hafs verse changes — the guard's error
  > names the source span, so the cut point is `reverse_ref` of each target word
  > until the Hafs ayah advances. Teaching `candidate_of` to dispatch on the
  > source span too is tracked as follow-up work.
- **A `by_ayah` chapter entry's ref is the delivery's verse, and MFA's word
  locations are Hafs.** They do not match for a renumbered surah, so the word
  filter reads the segment's `source_ref` span (`raw_v2._source_verse_keys`).

The hardcoded classifier tables are projected through `qua_domain`, never
duplicated per edition — see `services/reference/edition_tables.py`. Hafs is the
identity there (it returns the constants and never touches the package).

| Table | hafs | shuba | warsh / qalun |
|---|---|---|---|
| `muqattaat_words` | 30 | 30 | 30 |
| `muqattaat_verses` | 30 | 30 | **29** |
| `standalone_refs` | 10 | 10 | 10, four renumbered |
| `standalone_words` | 8 | 8 | 8, one respelled |
| `single_word_verses` | 28 | 28 | **3** |

> **A muqattaat verse is not always one word.** 13:1 runs on for eight more
> words after `الٓمٓر`. That is why the boundary-adjacency exemption is keyed on
> the **verse** (`muqattaat_verses`) while the `muqattaat` flag is keyed on the
> **word** (`muqattaat_words`). Do not collapse the two tables.

---

## 4. Which surfaces change

### Ingestion — offline alignment and promote

The batch pipeline lives in the `qua` monorepo; what crosses into this repo is the
staged run (`qua_shared/schemas/bucket/staged_run.py`) and
`scripts/bucket/promote_run.py`.

The riwayah is **declared, never inferred**. It rides `inputs.json`'s
`_meta.riwayah` (or `--riwayah`) into the run, lands on `RunManifest.inputs.riwayah`
*and* on every `candidates/<ch>.json`, and promote checks all three against each
other and against the catalog delivery row:

| Check | Where | On failure |
|---|---|---|
| Candidate file vs run manifest | `_promote_artifacts.read_entries` | build aborts |
| Run manifest vs `deliveries.riwayah` | `promote_run._guard_riwayah` | promote aborts — **`--force` does not cover it** |

Promote then stamps the Inspector-vocabulary slug onto `detailed.json` `_meta`,
`segments.json` `_meta` and `pipeline_meta.json`, and stamps the classifier fields
with `stamping.stamp_entries(..., riwayah=…)` so word counts and qalqala letters
come from the delivery's own edition. On Hafs every one of those keys is **absent**
— `None` is dropped by `exclude_none`, so a Hafs promote publishes the bytes it
always did and no backfill is owed.

The two MFA sidecars follow the same rule from the other side: they align against
each row's `source_ref` (Hafs), and `low_confidence_v2.json` is not produced at all
for a non-Hafs delivery (**D12**). `auto_split_v1.json` publishes the delivery's own
section refs while measuring their word counts in Hafs — the space the aligner's
word list lives in.

The extraction runbook is `.claude/skills/segments-extraction/` in the `qua` repo.

### Segments tab

Renders in the delivery's own script and font, with the edition's coordinates.
The reference bundle is per-edition and content-hashed:

- `GET /api/static/quran-refs/version?riwayah=<inspector slug>`
- `GET /api/static/quran-refs.json?riwayah=<inspector slug>&v=<hash>`
- `GET /api/static/edition/<inspector slug>/font` — 404 for Hafs (its font is
  inlined in the frontend bundle, because HF Spaces do not smudge Git-LFS at
  build time), 503 when the package is missing.

An unsupported `?riwayah=` is a **400**, never a Hafs fallback.

The bundle carries `verse_marker_prefix`: `۝` (U+06DD) for Hafs, whose Digital
Khatt font expects the ornament sent alongside the digits, and `""` for the three
packaged QPC fonts, which decorate the Arabic-Indic digits themselves. Sending
both renders two nested circles — the aligner app hit exactly this, which is why
the prefix is data and not a constant.

The frontend store holds **one** edition at a time and is cleared the moment a
different riwayah is requested; `sessionStorage` retains one bundle (~3 MB
against a 5–10 MB quota), so switching editions and back refetches by design.

### Validation

Every table read is per-edition. `basmala_amin` emits its sounded-Basmala
sub-check **only** where the edition numbers the Basmala as `1:1` (hafs, shuba);
the Amin check and the missed-Basmala augmentation stay for all four, resolving
the edition's last Fatiha verse rather than hardcoding 7.

Not produced for non-Hafs (**D12**): `low_confidence_v2.json`,
`ts_validation.json`. Both are tight-beam MFA probes against Hafs proxy phones —
the signal degenerates to "this is not Hafs". Registry rows stay, counts emit 0,
accordions hide. `auto_split_v1` **is** produced.

### Timestamps tab

Gate every letter/phoneme/tajweed surface on the **shard's own**
`_meta.profile`, not on the manifest riwayah — the manifest is a pre-fetch hint,
the shard is the truth about what timing exists. `isWordShard()` is that gate.

A word-profile delivery:

- renders through `WordTimedRow.svelte` (word cells + pause gaps), not the
  phonemizer cell renderer — `parse()` throws on a degenerate wire (**D8**);
  `word-shards.ts` lifts a trailing waqf mark off the word onto its gap
  (`WordProfileBoundary.stopSign`, shown once a pause is recorded) to mirror
  the native `stop_sign` column — except at a verse end, where the mark stays
  in the word and the verse marker owns the gap. A lone waqf mark is a
  zero-advance combining glyph, so it needs a per-(face, mark) scale + nudge to
  land in the middle of the tile: the cells package calibrates DigitalKhatt,
  and `lib/utils/waqf-render-table.ts` — generated by
  `scripts/codegen/regen_waqf_render.mjs`, which measures the painted ink in
  headless Chromium — calibrates the three QPC faces. A mark the face has no
  glyph for is absent from its table on purpose (Warsh and Qalun ship no
  U+06DA / U+06DB): the font stack falls through to DigitalKhatt, whose
  calibration is then the right one. Regenerate after a font bump;
- disables Letters, Phonemes, karaoke wipe and Tajweed with an explanatory title;
- locks the teleprompter to word-by-word animation with no shaped glyphs;
- shows a header badge naming the timings as Hafs-proxy word timings;
- offers only `audio` / `timing` / `silence` / `other` report categories, whose
  snapshots resolve through `ts_word_snapshot` (`verse` / `word` / `boundary`
  kinds only) — see [`ts-reports.md`](ts-reports.md);
- omits filmstrip coverage badges (the mushaf verse index is the Hafs one);
- gets word-by-word glosses reverse-projected server-side (**D9**) —
  `GET /api/qf/wbw/<s>/<a>?riwayah=<inspector slug>`, cached per
  `(verse_key, language, riwayah)`.

---

## 5. The word-profile shard

Two profiles share `timestamps/<chapter>.json.br` and the Brotli envelope,
discriminated by `_meta.profile`. **Absent means native** — every v13 object
predates the discriminator, and existing Hafs shards are never restamped.

```jsonc
{
  "_meta": {
    "schema_version": 14,
    "profile": "word",
    "chapter": 112,
    "riwayah": "qalun",
    "edition_id": "qaloon-v21+sdk-words-v1",
    "words_sha256": "…",
    "timing_provider": "hafs_proxy_mfa",
    "reference_riwayah": "hafs",
    "reference_id": "qul-text-qpc-hafs-312",
    "projection_id": "qua-edition-projection-v1",
    "projection_sha256": "…"
  },
  "readings": [{
    "id": "r1",
    "parts":      [["112:1", 0, 4880, 0, 4]],
    "words":      [["112:1:1", "قُلْ", 0, 1200], …],
    "boundaries": [[1, null], [1, null], [1, null], [3, 1]]
  }]
}
```

- `parts` — the same tuple as v13: `(ref, start_ms, end_ms, first_word_index, word_count)`.
- `words` — `(target_ref, exact_target_text, start_ms, end_ms)`. `0:0:N` for an
  unnumbered special (opening Basmala / Isti'adha ordinals).
- `boundaries` — one per word: `(state_code, verse_end)`, `state_code` indexing
  `("start", "join", "sakt", "stop")`.
- **Absent by construction:** `phonemizer_version`, `native_schema_version`,
  `renderer_codec_version`, `render`, `timing.s/a/c`, `native_profile`.

Boundary *timing* is derived by the same phoneme-free rule the native profile
uses, from the same helpers — never stored, never reimplemented.

Two facts about `parts` and `words` that every reader relies on:

- **A segment stays gapless across an unwritten word.** Warsh/Qalun do not
  write Hafs `57:24:10` (هُوَ); the reciter never says it, yet the Hafs proxy
  alignment still times it, carving that interval out of the neighbours' audio.
  `qua_sdk.integrations.word_shards` folds the span into the previous row
  (forward padding, like every other word boundary) — or into the next row's
  start when the absent word opens the reading. It never drops it: a hole there
  fails the publish's `intra_segment_gap` gate. Shards written before this rule
  were closed in place by `scripts/backfills/close_absent_word_gaps.py`.
- **A part's `t` is its segment's span, and the reader narrows it.** A segment
  crossing a verse boundary hands (some of) its whole span to each verse it
  touches, so parts of one reading overlap. `qua_shared.timestamps_native`
  clips every inner edge to the neighbouring part's words (lead-in stays with
  the first verse, trailing silence with the last) and splits occasions on
  first-word starts, not `t[0]`. Before that, Warsh `19:42` — one segment
  `19:40:1-19:42:11` then a retake `19:42:4-15` — read as two incomplete takes
  and was gated as "missing words" although coverage was complete.

`qua_shared/timestamps_word_audit.py` gates every write. Its important check is
the last one: word text must come from the edition index `words_sha256` names,
and every ref must be a real word of that edition. Without it a shard could
silently show one edition's script under another's coordinates.

---

## 6. Releases and the dataset

A word-profile delivery emits **verse + word** tiers only. The letter tier is
*absent*, not empty: a consumer must be able to tell "this recitation has no
letter timings" from "this verse happened to have none".

- `manifest.json` — each recitation carries `tiers` and `riwayah`;
  `content_hash` is taken over the **deepest emitted tier** (the shallower tiers
  are exact prefixes, so it still detects any timing change).
- `manifest.json.editions` — per non-Hafs edition: `edition_id`,
  `words_sha256`, `script_asset_sha256`, `font_family`, `projection_sha256`,
  `words_asset`, `font_asset`. A tier
  file's `script_sha256` carries `words_sha256`, not `script_asset_sha256`.
- Tier `_meta.script` names the edition index id (e.g. `warsh-v21+sdk-words-v1`)
  instead of `digital_khatt_v2`, with the matching digest.
- Release assets: `<riwayah>_words.json.gz` + `<riwayah>.ttf` per non-Hafs edition
  (`cut_release._edition_assets`), digested into `static_refs`, named by
  `editions[<riwayah>].words_asset` / `.font_asset`. No tier column in the CHANGELOG.
- The HF dataset stays config-per-mushaf under the riwayah folder (the viewer
  caps a config at 30 splits). The folder — the HF **config name** — is the
  INSPECTOR slug (`hafs_an_asim/<slug>-*`), which is where every split published
  so far lives; publishing the SDK slug would open a second `hafs/` folder,
  duplicate the config in the card frontmatter and orphan the old split.

Release schema 3 (the all-occurrence timeline, see
[dataset-and-releases.md](dataset-and-releases.md)) carries the multi-riwayah additions; the
release version is operator-chosen via `RELEASE_VERSION`, no schema-driven major gate (**D20**).

---

## 7. Verification fixtures

No non-Hafs recitation exists yet. `scripts/devenv/make_synthetic_delivery.py`
writes one from `qua_domain` itself — segs in the edition's coordinates with
their Hafs `source_ref`, `segments.json`, `pipeline_meta.json`, an audio
manifest, and optionally a word-profile shard written through the real producer
audit. It deliberately writes no `low_confidence_v2.json` / `ts_validation.json`,
so the absent-sidecar degradation is part of what the fixture proves.

`--riwayah` offers the three non-Hafs slugs only: a Hafs fixture would be the
identity projection and a Hafs word-profile shard is a contradiction (Hafs has a
native profile carrying cells, sounds and letter timings). Surah 112 — the
default `--chapter` — projects cleanly, so use a chapter that carries a real
relation to exercise `partial`: Warsh/Qalun `57:23` draws on Hafs `57:24`, whose
word 10 the target edition does not write.

---

## 8. Related

- [`shards.md`](shards.md) — the shard format in full
- [`validation.md`](validation.md) — the validation engine and its categories
- [`dataset-and-releases.md`](dataset-and-releases.md) — release adapters
- [`catalog.md`](catalog.md) — the `riwayahs` vocabulary and delivery slugs
- [`config-deploy.md`](config-deploy.md) — `QUA_DOMAIN_DEPLOY_KEY`, `INSPECTOR_MULTI_RIWAYAH`
