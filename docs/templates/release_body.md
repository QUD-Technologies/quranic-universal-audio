{{ release_title }}

> **License.** This release is distributed under the [QUA Dataset License 1.0](https://github.com/QUD-Technologies/quranic-universal-audio/blob/main/LICENSE-DATA). By downloading it you agree to its terms: any app or feature using this data, or a model trained, fine-tuned, evaluated or benchmarked on it, must be free for every user, fully accessible, and in an app with no advertising; the data may not be sold; derivatives share the same license; credit QUD Technologies - Qur'anic Universal Audio (https://qud.dev).

## What to download

| Asset | What it gives you |
|---|---|
| `manifest.json` | Release-level index: reciter zips, download URLs, checksums, sizes, coverage, and change type. |
| `catalog.json` | Release-level catalog: reciter names, riwayah, style, coverage, audio metadata, and the audio URLs paired with the timestamp data. |
| `<recitation>.zip` | One recitation's verse, word, and letter timestamp files, plus its own `catalog.json`. |
| `shard.py` | Optional helper that splits a large timestamp file into one JSON file per surah. |
| `check_updates.py` | Optional helper that checks the latest release for updates to the reciters you use; add `--sync` to re-download them. |
| `download_audio.py` | Optional helper that fetches a reciter's source audio (YouTube/Drive/CDN) re-encoded so the timestamps line up. |
| `surah_info.json` | Surah names, ayah counts, and word counts. |
| `digital_khatt_v2_script.json` | Exact DigitalKhatt V2 Hafs word text used by the timestamp projection. |
| `DigitalKhattV2.otf` | Matching DigitalKhatt V2 font (SIL Open Font License 1.1 in the font metadata). |
| `<riwayah>_words.json.gz` | Exact word text of a non-Hafs riwayah in this release (Warsh, Qalun, Shubah), keyed by its own `surah:ayah:word` coordinates; `manifest.json` `editions` names it. |
| `<riwayah>.ttf` | Matching KFGQPC font for that riwayah's script. |
| `LICENSE` | QUA Dataset License 1.0 (`qua-dataset-1.0`) full text. Covers the timing data; recitation audio is not covered. |

The release-level `manifest.json` and `catalog.json` index the whole release; each zip also carries its own `catalog.json` describing just that recitation.

{{ recitation_changes }}

> ⚠️ Missing verses are almost always upstream (the source omits it, audio issue, missing words, or reciter mistake). As such, we deliberately do not release their timings. This is usually discovered during alignment and review, and get manually flagged by a reviewer. The segments tab in the website should pinpoint the root cause of the issue/removal.

## How audio and timestamps pair

`catalog.json` contains the audio URLs for each recitation which can be streamed/downloaded directly, and every timestamp value is milliseconds relative to that matching source audio.

**Combined sources.** Most reciters serve one audio file per chapter, so each chapter's timestamps start at `0 ms` of its file. Some non-CDN sources (a YouTube video or Drive file holding several surahs) instead serve **one file for many chapters**. For those, `catalog.json` carries an `audio.chapter_offsets_ms` map: chapter `C`'s timestamps are relative to `chapter_offsets_ms[C]` inside `chapter_urls[C]`, i.e. `source_ms = chapter_offsets_ms.get(C, 0) + timestamp_ms`.

**Downloading source audio.** `download_audio.py` fetches a reciter's audio and re-encodes it to the same profile the alignment used (192 kbps CBR MP3, 44.1 kHz, mono by default), so the timestamps land correctly. Two layouts:

```bash
# one file per surah (001.mp3 … 114.mp3); timestamps apply directly (offset 0)
python download_audio.py catalog.json --reciter ibrahim_al_akhdar_drive

# one standalone clip per verse (001_001.mp3 …), cut from verse_timestamps.json.gz
python download_audio.py catalog.json --reciter ibrahim_al_akhdar_drive --format ayah

# the source files as published, plus a chapter -> file + offset map
python download_audio.py catalog.json --reciter ibrahim_al_akhdar_drive --format original
```

It uses `yt-dlp` + `ffmpeg` for YouTube/Drive sources and needs neither for direct CDN MP3s. `--bitrate`, `--sample-rate`, and `--channels` are configurable; see `--help`.

The `ayah` layout needs the verse tier file; run it from the unzipped reciter zip so `verse_timestamps.json.gz` sits next to `catalog.json` (or pass `--timestamps`).

## Timestamp levels

| File | Use it when you need | Why it is separate |
|---|---|---|
| `verse_timestamps.json.gz` | verse playback or verse clips | smallest download |
| `word_timestamps.json.gz` | word highlighting | faster than loading letters when you only need words |
| `letter_timestamps.json.gz` | letter animation | exact DigitalKhatt text plus timed Unicode-scalar paint ranges |

The files are split and gzipped for storage, speed, and network efficiency. Download only the level you need.

Not every recitation ships all three. `manifest.json` lists each recitation's `tiers`.

Use `shard.py` when your app prefers per-surah files locally:

```bash
python shard.py word_timestamps.json.gz --out-dir per_surah
```

By design, timestamps have no gaps between them except at pauses, making highlighting appear smooth and continuous during one breath. 

## Programmatic use

Read `manifest.json`, choose a reciter from `recitations`, download its `zip_url`, and verify the zip with `sha256`.

Use `catalog.json` when you need display names, coverage, audio metadata, or the source audio URLs that the timestamps refer to.

<details><summary>Reciter zip schemas</summary>

Each reciter zip contains `catalog.json` and three timestamp files.

```ts
type VerseKey = "surah:ayah";
type Ms = number;
type Word = [word_idx: number, start_ms: Ms, end_ms: Ms];
type ScalarRange = [from: number, to: number]; // half-open [from,to)
type AnimationToken = [
  word_occurrence: number,
  start_ms: Ms,
  end_ms: Ms,
  owns_sound: boolean,
  paint: ScalarRange[],
];

type VerseOccurrence = [
  ref: VerseKey, start_ms: Ms, end_ms: Ms, canonical: boolean, silence_after_ms: Ms
];
type WordOccurrence = [
  ...verse: VerseOccurrence,
  words: Word[]
];
type LetterOccurrence = [
  ...word: WordOccurrence,
  text: string,
  tokens: AnimationToken[],
];

type VerseTimestamps = { _meta: Meta & { tier: "verse" }, rows: VerseOccurrence[] };
type WordTimestamps = { _meta: Meta & { tier: "word" }, rows: WordOccurrence[] };
type LetterTimestamps = {
  _meta: Meta & { tier: "letter", script: "digital_khatt_v2", unicode_indexing: "scalar" },
  rows: LetterOccurrence[]
};
```

The three tiers describe the same ordered occurrences at increasing detail. `WordOccurrence` is exactly `VerseOccurrence + [words]`; `LetterOccurrence` is exactly `WordOccurrence + [text, tokens]`. Every number is milliseconds from the start of the source audio.

Rows are a playback timeline: one row per contiguous recited span, in audio order. A reciter who repeats a verse, restarts it, or recites only part of it produces one row per span; exactly one row per verse is `canonical` — the earliest complete, continuous take. `rows.filter(r => r[3])` yields a clean one-take-per-verse dataset. Within-take lookbacks (a jump back mid-verse) stay inside their row, where `word_idx` repeats or steps backwards. `silence_after_ms` is the gap to the next row in the same chapter timeline, `0` for the chapter's last row; after filtering rows, recompute it from the kept set (`next.start_ms - end_ms`).

**Verse tier** — audible occurrence spans plus the gap until the next occurrence in the same chapter timeline:

```jsonc
{
  "_meta": { "schema_version": 3, "slug": "example_reciter", "riwayah": "hafs", "units": "ms", "tier": "verse", "verse_count": 6236, "occurrence_count": 6301, "script": "digital_khatt_v2", "script_sha256": "…", "unicode_indexing": "scalar" },
  "rows": [
    // [ref, start_ms, end_ms, canonical, silence_after_ms]
    ["1:1", 70, 2790, true, 41],
    ["1:2", 2831, 5100, true, 900],
    ["1:2", 6000, 8300, false, 120]   // the reciter repeated 1:2
  ]
}
```

**Word tier** — the verse row, then one `[word_idx, start_ms, end_ms]` per recited word:

```jsonc
{
  "_meta": { "schema_version": 3, "slug": "example_reciter", "riwayah": "hafs", "units": "ms", "tier": "word", "verse_count": 6236, "occurrence_count": 6301, "script": "digital_khatt_v2", "script_sha256": "…", "unicode_indexing": "scalar" },
  "rows": [[
    "1:1", 70, 2790, true, 41,
    [
      [1,   70,  770],   // بِسْمِ
      [2,  770, 1280],   // ٱللَّهِ
      [3, 1280, 2050],   // ٱلرَّحْمَٰنِ
      [4, 2050, 2790]    // ٱلرَّحِيمِ
    ]
  ]]
}
```

`word_idx` is 1-based within the verse. When a reciter loops back or re-recites part of a verse, `word_idx` can repeat or step backwards.

**Letter tier** — the complete word row followed by exact DigitalKhatt text and a flat list of timed paint units. `word_occurrence` is a zero-based position in the adjacent `words` array, so repeated `word_idx` values stay unambiguous. `paint` contains half-open Unicode-scalar ranges into that occurrence's `text`:

```jsonc
{
  "_meta": { "schema_version": 3, "slug": "example_reciter", "riwayah": "hafs", "units": "ms", "tier": "letter", "verse_count": 6236, "occurrence_count": 6301, "script": "digital_khatt_v2", "script_sha256": "…", "unicode_indexing": "scalar" },
  "rows": [[
    "1:1", 70, 2790, true, 41,
    [ [1, 70, 770], [2, 770, 1280], [3, 1280, 2050], [4, 2050, 2790] ],
    "بِسْمِ ٱللَّهِ ٱلرَّحْمَٰنِ ٱلرَّحِيمِ",
    [
      [0,  70, 300, true, [[0, 2]]],
      [0, 300, 560, true, [[2, 4]]],
      [0, 560, 770, true, [[4, 6]]]
      // ... remaining timed paint units
    ]
  ]]
}
```

The text itself is the vocabulary: there is no separate letter-vocabulary file. A token is a producer-attributed animation unit, not one Unicode character. Its paint ranges can cover a base with combining marks or only an independently sounded mark. Presentation-only signs remain in `text` without a timed paint owner.

Ranges use Unicode scalar indexes, not UTF-16 code units. In JavaScript, index `Array.from(text)` rather than indexing the string directly.

</details>

<details><summary>Catalog and manifest schemas</summary>

`manifest.json` is release-level only — the download index. `catalog.json` exists at two grains: the release-level file indexes every recitation, and each zip carries a per-recitation copy scoped to itself.

**Release-level `manifest.json`** — the download index (one entry per reciter, with its `zip_url`):

```ts
type ReleaseManifest = {
  schema_version: 3;
  release_version: string;
  recitation_count: number;
  static_refs: Record<string, { sha256: string; bytes: number }>;
  recitations: Record<string, {
    zip: string;
    zip_url: string;        // direct download URL for this reciter's zip
    sha256: string;
    bytes: number;
    coverage_ayahs: number;
    change_kind: "added" | "refresh" | "unchanged";
  }>;
  license: "qua-dataset-1.0";
};
```

```jsonc
{
  "schema_version": 3,
  "release_version": "v4.0.0",
  "recitation_count": 13,
  "recitations": {
    "example_reciter": {
      "zip": "example_reciter.zip",
      "zip_url": "https://github.com/<owner>/<repo>/releases/download/v4.0.0/example_reciter.zip",
      "sha256": "…", "bytes": 1234567,
      "coverage_ayahs": 6236,
      "change_kind": "added"
    }
  }
}
```

**Release-level `catalog.json`** — reciter metadata plus the source audio URLs the timestamps refer to:

```ts
type ReleaseCatalog = { schema_version: 3; recitations: ReciterCatalog[] };
type ReciterCatalog = {
  schema_version: 3;
  slug: string;
  name_en?: string; name_ar?: string;
  riwayah?: string; style?: string; channel?: string;
  audio_category?: "by_surah" | "by_ayah";
  audio: {
    chapter_urls: Record<string, string>;        // chapter number -> source audio URL
    chapter_offsets_ms?: Record<string, number>; // present only for combined sources:
                                                 // chapter -> ms offset within its (shared) source file
    sample_rate_hz?: number; channels?: number;
    bitrate_mode?: string; bitrate_kbps_nominal?: number;
  };
  coverage: {
    surahs: number; ayahs: number;
    missing_surahs?: string;  // surahs not covered at all, e.g. "1-84" or "4,7,9,37,39-40,45,65"
    missing_verses?: string;  // within-surah gaps, e.g. "75:18-40" or "7:116, 41:15"
  };
};
```

`coverage.missing_surahs` and `coverage.missing_verses` describe what a recitation does **not** cover in concise `surah` / `surah:ayah` notation (consecutive numbers collapse as `18-40`). A whole missing surah appears only in `missing_surahs`; a partly-covered surah's gaps appear only in `missing_verses`. Both keys are omitted when the recitation is complete.

```jsonc
{
  "schema_version": 3,
  "recitations": [
    {
      "slug": "example_reciter",
      "name_en": "Example Reciter", "name_ar": "...",
      "audio_category": "by_surah",
      "audio": { "chapter_urls": { "1": "https://cdn.example/001.mp3", "2": "https://cdn.example/002.mp3" } },
      "coverage": { "surahs": 114, "ayahs": 6236 }
    }
  ]
}
```

**Per-recitation `catalog.json` (inside each zip):** a single `ReciterCatalog` — the same object as one entry of the release-level `recitations` array.

</details>

## Staying up to date

We occasionally fix issues or batch-refresh a reciter's timestamps with an improved alignment model, so a reciter you already use can change in a later release. Three ways to keep track:

- **Custom events** - use the Email notifications feature on the website to subscribe to custom events.
- **All releases** - click **Watch -> Custom -> Releases** at the top of the GitHub repository. GitHub emails you on every release, and the notes above always list which reciters were added or refreshed.
- **Only the reciters you use** - run `check_updates.py` against the `manifest.json` you downloaded. It exits non-zero when any of your reciters changed, so a scheduled GitHub Action or CI job notifies you automatically; add `--sync` to also re-download the changed zips.

```bash
# report which of your reciters changed (exit 1 if any)
python check_updates.py manifest.json --reciters mishary_rashid_al_afasy_mp3quran

# or keep your local copy in sync automatically
python check_updates.py manifest.json --sync
```

{{ release_footer }}
