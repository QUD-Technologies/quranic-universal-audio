# Timestamp generation

Timestamps come from the neural timing head on the aligner Space. Each chapter's
segment times are stored beside its shard, `reciters/<slug>/timing/<chapter>.json.br`
(one entry per `segment_uid`: the ref and span it was timed with, words with their
letters and sounds, the model). Every call goes through the aligner's
`POST /api/v1/extraction/timing` (`services/timing/aligner_timing.py`): it receives a
chapter's detailed.json entries, their bucket audio and the stored times, keeps every
time whose segment is unchanged and times the rest.

- **Align** stores them: the run's assemble stage times every chapter's final segments.
- **Saves and undos** re-time their chapter in the background
  (`services/timing/retime_queue.py`); only the changed segments are timed.
- **Segment cards** read them back (`GET /api/seg/word-times/<reciter>/<chapter>`) and
  light the sounding word while a card plays.
- **A timestamps run** (`services/admin/ts_aligner_runner.py`) asks for the chapter's
  native v13 shards as well; with the times already current it only builds them. A
  chapter with a failed segment keeps its previous shards and fails the run. The run
  writes the same run-log record (`reciters/<slug>/jobs/ts/<run_id>.json`) the batch
  Space wrote, so completion, releases and the automations
  (`services/admin/timestamps_jobs.py`) are unchanged.

Bulk re-timing for a new model is an offline Katana batch (qua `engines/timing-batch`,
`qua_timing_batch.retime`) that writes the same two files.

`INSPECTOR_TS_ENGINE=space` sends runs to the MFA batch timing Space instead
(`services/admin/ts_space_client.py`, `/internal/v1/timestamps`), which writes
shards and `ts_validation.json` directly. QUA is a pure consumer of the shards. The
complete stored contract is [shards.md](shards.md).

## Responsibilities

The producer owns acoustic work only:

- resolve recorded segments and their connected-wasl relationships;
- obtain chapter audio;
- time each segment with the neural timing head (`timing.neural_head@v1`);
- recover word, sound, and written-letter intervals;
- pass timing occurrences to the SDK v13 builder;
- validate and deterministically Brotli-compress each chapter;
- stage result objects and validation metadata.

It does not construct frontend cells, rename tajweed rules, synthesize bridges, add silent flags, or assign renderer ownership.

## Native build

The aligner's chapter route passes the stored times, as timing occurrences, to the SDK v13 shard builder.

For each chapter the builder:

1. Orders original occurrences by absolute audio time.
2. Joins adjacent occurrences while the preceding occurrence carries `wasl`.
3. Phonemizes each maximal connected reading once with the pinned quranic-phonemizer.
4. Builds native schema-2 analysis, source, and transformed-cell documents using `emphatic_fatha`, `emphatic_ikhfaa`, `imala`, and `tashil` for display.
5. Checks the recovered acoustic sound sequence against the acoustic native surface.
6. Transfers word and sound intervals to native IDs and recuts written-letter intervals to source-unit IDs.
7. Runs the schema and identity-closure audit, proves deterministic Brotli
   quality-6 bytes, and atomically replaces the chapter object.

Cross-verse wasl is never split or rephonemized as pausal. Known chains such as `1:3→1:4`, `14:1→14:2`, and the connected chapter-79 chain are release gates.

## Version pinning

The aligner Space installs the same-commit qua SDK and its quranic-phonemizer pin; a
chapter's shard `_meta` records the schema version, native schema version, renderer
codec version, phonemizer version and stop edition it was built with, and the stored
times record the timing model and phonemizer they were decoded with (a change of either
re-times the chapter). The additional display phonemes are same-cardinality notation
choices and never enter the timing model or redistribute intervals.

## Inputs and outputs

The input is the reviewed timestamp source plus chapter audio. Chapter audio uses the bucket object first and the manifest URL only as a transient fallback.

The output is:

```text
reciters/<slug>/timestamps/<chapter>.json.br
```

The normal job writes each selected chapter to the active reciter prefix only
after its complete replacement bytes pass validation. A chapter failure leaves
the prior object intact. Affected-chapter regeneration does not rewrite other
chapters.

## Single-flight and stale runs

Launching is gated on the reciter's newest `reciters/<slug>/jobs/ts/<run_id>.json`
record: a `running` one refuses the next launch with 409 "a timestamps job is
already running". The **Space** owns that record — it stamps `succeeded` /
`failed` when the run ends — so a Space restart or rebuild mid-run leaves
`running` behind with nothing left to finish it, and the slug can never be
relaunched.

`timestamps_jobs.running_job_for` therefore applies a staleness ceiling: a
`running` record whose `started_at` is older than `INSPECTOR_TS_STALE_RUN_HOURS`
(default **6 h**; a full 114-chapter run lands in ~1-2 h) reads as dead, and both
the launch guard and the in-flight jobs registry ignore it. The record itself is
left alone — **Cancel** in the run drawer is still what rewrites it to
`canceled` and keeps the history honest.

## Failure policy

The following block a connected reading or reciter:

- stored/native word identity drift;
- sound count, order, or selected-surface token drift;
- a non-unique source-letter recut;
- an unresolved native timing ID;
- an invalid native document or shard schema;
- nondeterministic Brotli output;
- an unresolved or ambiguous report target.

There is no nearest-token, nearest-cell, glyph-first, or positional fallback. Only a true sound count/sequence change authorizes realignment of the affected connected reading.

## Historical v11-to-v12 restamp

`scripts/migrations/restamp_timestamps_v12.py` is a local cutover tool. It reads complete historical v9/v11 chapters, reconstructs maximal connected readings, validates the v9 acoustic or v11 display token profile exactly, preserves all intervals, emits only v12, and runs the normal v12 audit.

The tool is not a server compatibility reader. It requires a fresh output directory and can require an exact chapter count. The two historical seen/saad choices and heavy rāʾ at `89:4:3` are explicit restamp policy because v11 timed those readings that way; fresh v12 generation uses the phonemizer 2.15 defaults.

Example:

```powershell
$env:PYTHONPATH='C:\path\to\quranic-universal-audio;C:\path\to\qua\packages\sdk\src'
python scripts/migrations/restamp_timestamps_v12.py C:\staging\v11 C:\staging\v12 --require-chapters 114 --summary C:\staging\audit.json
```

Nothing is uploaded by this command.

## Acceptance

Before a complete corpus is promoted to the v13 prefix:

- all 114 chapters validate;
- old and native word/sound intervals are byte-identical;
- all letter rows recut uniquely;
- all connected-wasl tests pass;
- canonical release projection closes over every verse;
- every existing report maps exactly to a native target;
- two serializations of every chapter produce identical Brotli bytes.

The cutover process and report migration are documented in [data-migrations.md](data-migrations.md) and [ts-reports.md](ts-reports.md).


## Multi-riwayah

The run request names the delivery's edition: `ts_space_client.start_run` puts
`riwayah` (SDK slug) in the signed body **only when it is not Hafs**. The JCS
canonicaliser sorts keys and the field is omitted for Hafs, so every existing
Hafs preimage stays byte-identical and the Space-side change need only ship
before the first non-Hafs run, not in lockstep.

The Space aligns against Hafs as a proxy, phonemizes Hafs phones, then projects
onto the delivery's words. What comes back is a **word-profile** shard: words and
pause boundaries, no cells, sounds or animation tokens. `low_confidence_v2.json`
and `ts_validation.json` are not produced for it — both are tight-beam probes
against Hafs proxy phones, so the signal degenerates to "this is not Hafs".

Full detail: [`editions.md`](editions.md).
