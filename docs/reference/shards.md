# Timestamp shards

Timestamp shards store a compact, renderer-neutral projection of native
phonemizer readings plus audio timing. Full native documents are generation
and audit inputs; they are not repeated in every reciter's runtime shards.

## Two profiles

Each object at `reciters/<slug>/timestamps/<chapter>.json.br` is one of two
profiles, discriminated by `_meta.profile`:

| Profile | Schema | Contains | Produced for |
|---|---|---|---|
| `native` | 13 or 14 | full phonemizer cells, sounds, animation tokens | Hafs |
| `word` | 14 | proxy-timed words + pause boundaries, nothing below | the other three riwayat |

**Absent `profile` reads as `native`.** Every v13 object predates the
discriminator, and existing Hafs shards are never restamped, so a reader must
branch on `shard_profile()` / `isWordShard()` and never on `schema_version`.

The word profile is documented in full in
[`editions.md`](editions.md#5-the-word-profile-shard); the rest of this page is
the native profile.

## Contract

A native object is a closed schema-v13/v14 JSON document compressed with
deterministic Brotli quality 6:

```json
{
  "_meta": {
    "schema_version": 13,
    "chapter": 1,
    "audio_category": "by_surah",
    "phonemizer_version": "3.0",
    "native_schema_version": 2,
    "renderer_codec_version": 1,
    "native_profile": {
      "riwayah": "hafs",
      "script": "uthmani",
      "variant": {},
      "extra_phonemes": ["emphatic_fatha", "emphatic_ikhfaa", "imala", "tashil"]
    }
  },
  "readings": [{
    "id": "r1",
    "parts": [["1:3", 1200, 5400, 0, 2]],
    "render": {
      "v": 1,
      "m": ["1:3", "canonical-digest", "native-documents-sha256"],
      "p": [], "r": [], "w": [], "b": [], "a": []
    },
    "timing": {"w": [], "s": [], "a": [], "c": []}
  }]
}
```

v13 supersedes v12 by replacing the per-letter timing array (`timing.l`) with
producer-owned **animation tokens** (`render.a` + `timing.a`). Everything else
about the codec — parts, words, boundaries, columns, sounds, the deterministic
Brotli envelope — is unchanged from v12, so the two differ only in how sub-word
paint is expressed.

There is no legacy reader. Historical v9/v11/v12 objects are accepted only by
the one-time restampers, which emit the current shape and validate it before
upload.

`_meta.stop_edition` names the Madinah printing whose waqf marks the stop
cells carry: `"1421"` (Digital Khatt's, stamped from quranic-phonemizer 3.0.1)
or absent, meaning the earlier `"1405"`. The two differ on 352 words and only in
what a shard renders; the Timestamps view takes the drawn mark from the Digital
Khatt word, so either edition displays correctly. Shards the re-time does not
rebuild are restamped locally with `scripts/migrations/restamp_stop_edition.py`
(no re-timing; dry-run unless `--output`).

## Why the compact codec is native

The SDK builds the complete schema-v2 `analysis`, `source`, and `cells`
documents first. Codec 1 then removes only fields unused by the renderer or
audio surfaces and packs repeated records into fixed-position tuples. It does
not regroup cells, rename rules, infer ownership, or match glyphs.

`@quranic-phonemizer/cells` owns `decodeCompact()` and `parseCompact()`. The
decoded payload enters the same schema-v2 `parse()` path as live phonemizer
documents. The Inspector therefore has no cell adapter. Python consumers use
`qua_shared.timestamps_codec` for the same storage contract.

Before encoding, the SDK proves for the whole reading that word, sound, rule
occurrence, source-unit, and analysis-boundary IDs equal their array positions;
cell words are positional, post-word boundary IDs equal `word_id + 1`, and all
timing rows follow native ID order. Column IDs are not positional and remain
explicit. Any failed invariant blocks generation.

## Reading tuples

A reading spans the maximal connected chain in the recording. A segment whose
`wasl` flag connects to the next segment stays in the same reading, including
cross-verse wasl. The full chain is phonemized once.

Each part is:

```text
[ref, start_ms, end_ms, first_word_id, word_count]
```

The word IDs are the contiguous range beginning at `first_word_id`. Retakes
and loopbacks remain separate readings or parts. Cross-verse focus changes
opacity and editability only; it never changes phonemes or geometry.

## Renderer payload

`render` has these keys:

| Key | Meaning |
| --- | --- |
| `v` | Compact codec version, always `1`. |
| `m` | `[reading_ref, canon_digest, native_documents_sha256]`. |
| `p` | Phoneme tokens indexed by native sound ID. |
| `r` | Producer rule IDs indexed by native rule-occurrence ID. |
| `w` | Words indexed by native word ID. |
| `b` | Post-word boundaries; item `i` has native boundary ID `i + 1`. |
| `a` | Animation tokens indexed by token ID (v13; see [Timing payload](#timing-payload)). |

A word is:

```text
[ref, text, columns, sounds, groups, runs, bridges]
```

A post-word boundary is:

```text
[state_code, columns, sounds, bridges, verse_end, exclusive_group]
```

Columns use:

```text
[id, role_code, text, source_unit_ids, slot_ids, tier_code,
 attached_column_id, status_code, anchor_unit_id, side_code,
 owned_sound_ids, presented_sound_ids, rule_occurrence_ids, silence]
```

The enum tables are defined together in the SDK encoder and renderer decoder.
`silence` is an occurrence ID, `null`, `-1` for `orthographic_silence`, or `-2`
for `variant_silence`. Sounds, groups, runs, and bridges retain every native ID
and ordered span. Source-character IDs and selected-variant annotations are
omitted because no renderer or Inspector surface reads them; the resolved
variant profile remains in `_meta` for reproducibility.

## Timing payload

`timing` uses positional arrays:

| Key | Tuple | Meaning |
| --- | --- | --- |
| `w` | `[start_ms, end_ms]` | Word span indexed by word ID. |
| `s` | `[start_ms, end_ms]` | Sound span indexed by sound ID. |
| `a` | `[start_ms, end_ms]` | Animation-token span indexed by token ID; both ends are nullable together (an untimed token). |
| `c` | `[column_id, start_ms, end_ms]` | Sparse exact column-span override; a null pair suppresses a derived span. |

`render.a` carries the matching token metadata, positionally aligned with
`timing.a`:

```text
[word_id, source_unit_ids, character_ids, paint_character_ids, text,
 sound_ids, policy_code, target_token_id]
```

`policy_code` indexes `("timed", "cohighlight_previous", "cohighlight_next")` —
a token either owns its own interval or borrows a neighbour's, which is how a
combining mark rides its base rather than detaching into a span of its own.
`character_ids` are the token's source characters; `paint_character_ids` is the
subset that actually receives paint. `target_token_id` names the token a
co-highlight defers to, and is `null` for a `timed` token.

A sounding column is timed exclusively by its native cell sounds; source-unit
timing is only the fallback for a soundless column. During encoding the SDK
compares the letter-only fallback with the full source-unit fallback and stores
an entry in `c` only when they differ. This prevents an attached or inserted
mark from extending a neighbouring sounding cell while preserving exact timing
for genuinely soundless columns.

Boundary timing is derived losslessly: each internal boundary runs from the
preceding word end to the following word start. A reading's final boundary runs
through the next reading's **first word start** when the recording leaves an
inter-reading gap; using the next part start is incorrect because aligned words
may extend outside or overlap source segment bounds. This rule is independent
of verse identity, so same-verse retakes and cross-verse transitions behave the
same. The chapter-leading boundary spans its first part start to its first word
start, and the chapter-final boundary spans its last word end to its final part
edge. Overlaps clamp to an empty interval. Boundary timing never changes the
native semantic state.

## Renderer policy is not shard schema

The shard excludes names, localized labels, legend groups, colors, duration
labels, toggles, underline stacks, and renderer options. The Inspector uses:

```ts
parseCompact(reading.render, defineInspectorRule, {
  iqlabTanween: 'mini-meem',
  iqlabNoon: 'mini-meem',
  openTanween: true
})
```

The shared package owns native groups, compact mergers, sakt extraction,
Digital Khatt boundary text, iqlab display synthesis, and tanween glyph choice.
The Inspector owns timing, highlighting, seeking, looping, reports,
translations, context opacity, locale, and rule presentation. Stable
`data-qc-*` hooks address words, columns, sounds, groups, boundaries, and
bridges; group keys are ordered native column IDs.

## Generation and serving

```text
raw timing occurrences
  -> maximal connected readings
  -> full native schema-v2 documents
  -> exact timing transfer and source-unit recut
  -> compact codec 1 + sparse timing overrides
  -> deterministic Brotli quality 6
```

The main entry points are:

| Purpose | Entry point |
| --- | --- |
| Native documents | `qua_sdk.integrations.native` |
| Compact encoder | `qua_sdk.integrations.cells_codec` |
| v13 builder | `qua_sdk.integrations.shards.build_native_shards` |
| Validated atomic writer | `qua_shared.timestamps_shards.write_validated_shard` |
| Structural audit | `qua_shared.timestamps_v13_audit` |
| Python decoder | `qua_shared.timestamps_codec` |
| Canonical release projection | `qua_shared.timestamps_native` |

The Flask shard route passes the stored bytes through as `application/json`
with `Content-Encoding: br`; browsers perform HTTP decompression and parse
normal JSON. Manifest and reference-resource compression remain independent.

Fresh MFA generation and the one-time restamper share the same structural
audit and deterministic serializer. The active chapter is replaced atomically
only after both checks pass. Generation also pins the staged SDK by clean Git
revision in `.github/config/repo.yml`; matching schema 13 alone is not enough.

## Production restamp runbook (historical, v11 to v12)

Retained as the worked example of a shard cutover; the v12-to-v13 restamp
followed the same shape. A cutover is backup-first and uses a frozen source.
Never restamp
from live paths while files can still change.

1. Inventory every production `reciters/<slug>/timestamps/` tree. Record the
   per-reciter filename/size map, total files, and total bytes.
2. Choose a unique UTC-labelled backup prefix and prove it does not exist.
3. Copy each timestamp directory server-side with the repository bucket tool:

   ```powershell
   python scripts/bucket/bucket_cp.py `
     reciters/<slug>/timestamps `
     backups/timestamps-v11-pre-v12-<UTC>/reciters/<slug>/timestamps `
     --bucket prod --yes-prod
   ```

4. List source and backup independently and require identical relative
   filenames and byte sizes for every reciter. A copy command's success message
   alone is not a backup verification.
5. Download from the verified backup prefix into local staging. Pin the
   generation environment to `quranic-phonemizer==2.15.3` and the exact SDK
   cutover commit, then run:

   ```powershell
   python scripts/migrations/restamp_timestamps_v12.py `
     <stage>/v11/<slug> <stage>/v12/<slug> `
     --require-chapters <backed-up-source-count> `
     --summary <stage>/summaries/<slug>.json
   ```

6. Require one summary per source reciter, identical source/output chapter
   sets, schema/native/codec versions `12/2/1`, phonemizer `2.15.3`, structural
   audit success, deterministic Brotli, and byte-identical timing intervals.
   Complete reciters require all 114 chapters. Pre-existing partial reciters
   remain explicitly partial; missing chapters are never fabricated.

Normal timestamp jobs enforce the same producer pin through the staged
`run_generate_timestamps.py` entrypoint. It repairs a stale prebuilt image
before importing the generator; SDK staging rejects the wrong Git revision
before uploading any part of that checkout.

The 2026-08-24 production freeze is
`backups/timestamps-v11-pre-v12-20260824T133756Z`: 37 reciters, 4,126 files,
and 282,951,608 bytes. Source and backup filename/size maps matched exactly.
Thirty-five reciters have 114 chapters; `islam_sobhi_mp3quran` has 106 and
`ahmed_saud_mp3quran` has 30.

The matching local v12 acceptance set contains 4,126 `.json.br` files and
483,452,080 bytes. All 37 reciter summaries are `ok`; every document stamps
schema/native/renderer-codec `12/2/1` and phonemizer `2.15.3`. Its SHA-256
upload inventory is retained with the cutover working set as
`v12-final-inventory.json` and must match the staged bucket prefix before the
consumer switch.

### Coordinated cutover and rollback

Do not delete or overwrite `.json.gz` during the first v12 release. Upload the
audited `.json.br` files alongside them only after every code PR and the audio
CI gate is green. The v11 application continues to read `.json.gz` while that
upload completes. Then merge/deploy the v12-only SDK, renderer, and Inspector
changes as one coordinated consumer switch. This makes the visible cutover the
application deployment, not thousands of sequential bucket writes.
The deploy workflow synchronizes the renderer repository's read-only
`CELLS_DEPLOY_KEY` into each Docker Space before upload; the Docker build reads
it through a build-secret mount and never stores it in an image layer.

Rollback is the inverse consumer switch: redeploy the v11 application, which
still finds the untouched `.json.gz` objects. If a legacy object ever needs
restoration, copy its timestamp tree back from the verified backup prefix with
`bucket_cp.py`. Remove v11 objects only after production acceptance and a
separate, explicitly approved cleanup.

## Validation and cutover gate

For every backed-up chapter, the audit requires the target schema/native schema 2/
codec 1, complete ID closure, byte-identical word and sound intervals, exact
letter recutting, complete part coverage, valid sparse overrides,
deterministic Brotli output, retained known cross-verse wasl chains, and zero
heuristic fallbacks. Complete reciters additionally require all 114 objects.
A sound-count or sound-order mismatch blocks that reading and is the only
reason to consider acoustic realignment.
