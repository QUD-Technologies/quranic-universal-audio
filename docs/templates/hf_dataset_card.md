---
license: other
license_name: qua-dataset-1.0
license_link: https://github.com/QUD-Technologies/quranic-universal-audio/blob/main/LICENSE-DATA
extra_gated_heading: Accept the QUA Dataset License 1.0 to access this dataset
extra_gated_prompt: |
  This dataset is distributed under the **QUA Dataset License 1.0** by QUD Technologies. By requesting access you agree to its terms, including:

  - **Free access.** Any app or feature that uses this data, or a model trained, fine-tuned, distilled, evaluated or benchmarked on it (or on such a model's output), must be free for every user: no charge, not behind, restricted to, or enhanced in a paid tier, no partial or gated access. The app itself must be free; other, unrelated features may be paid.
  - **No advertising** anywhere in an app that uses this data or such a model.
  - **No selling** the data or access to it, including through a paid API or to another business.
  - **Share-alike.** Derivatives, including models, may only be distributed under this license.
  - **Declaration and disclosure.** Published models and datasets must state they used QUA, and you must answer truthfully if QUD Technologies asks whether a model or feature used it.
  - **Attribution** to QUD Technologies - Qur'anic Universal Audio (https://qud.dev).

  Recitation audio, Qur'an text editions and fonts are not covered by this license and remain under their own terms. Read the full license before accepting.
extra_gated_fields:
  Name: text
  Organisation (or "individual"): text
  Intended use:
    type: select
    options:
      - Academic or non-profit research
      - App or product feature
      - Model training or evaluation
      - Personal study
      - label: Other
        value: other
  I have read and agree to the QUA Dataset License 1.0, including its free-access and no-advertising conditions: checkbox
extra_gated_button_content: Accept and access
task_categories:
- automatic-speech-recognition
language:
- ar
tags:
- quran
- forced-alignment
- word-timestamps
- letter-timestamps
- audio-segmentation
- asr
pretty_name: Qur'anic Universal Ayahs
size_categories:
- 100K<n<1M
{{configs}}
---

<p align="center">
  <a href="https://aligner.qud.dev/"><img src="https://img.shields.io/badge/%F0%9F%A4%97%20Tool-Qur'anic%20Universal%20Aligner-E8C32E" alt="Tool - Qur'anic Universal Aligner"></a>
  <a href="https://audio.qud.dev/"><img src="https://img.shields.io/badge/%F0%9F%A4%97%20Website-Qur'anic%20Universal%20Audio-E8C32E" alt="Website - Qur'anic Universal Audio"></a>
  <br>
  <a href="https://github.com/QUD-Technologies/quranic-universal-audio"><img src="https://img.shields.io/badge/Recitations-{{recitations}}-d4842a" alt="Timestamped recitations"></a>
  <a href="https://github.com/QUD-Technologies/quranic-universal-audio"><img src="https://img.shields.io/badge/Riwayat-{{riwayat}}-f0ad4e" alt="Timestamped riwayat"></a>
  <a href="https://github.com/QUD-Technologies/quranic-universal-audio"><img src="https://img.shields.io/badge/Hours-{{hours}}-d4842a" alt="Timestamped audio hours"></a>
  <br>
  <a href="https://github.com/QUD-Technologies/quranic-universal-audio/releases/latest"><img src="https://img.shields.io/github/v/release/QUD-Technologies/quranic-universal-audio?label=Release&color=4a5568" alt="Latest Release"></a>
  <a href="https://github.com/QUD-Technologies/quranic-universal-audio"><img src="https://img.shields.io/github/stars/QUD-Technologies/quranic-universal-audio?style=social" alt="GitHub stars"></a>
  <a href="https://github.com/QUD-Technologies/quranic-universal-audio/blob/main/LICENSE-DATA"><img src="https://img.shields.io/badge/License-QUA%20Dataset%201.0-4a5568" alt="License"></a>
</p>

<h1 align="center">Qur'anic Universal Ayahs</h1>

Qur'anic Universal Audio (QUA) is a project that unifies recitations on the internet and generates timing data using forced alignment — community-verified results and constantly expanding dataset.

This dataset pairs ayah by ayah audio with word-level timestamps, DigitalKhatt letter-animation timestamps, and waqf-aware segment data. Repeated words are preserved in `text_uthmani` and `word_timestamps`, so the row reflects what the reciter actually recited rather than a plain copy of canonical ayah text.

> **Tip:** Click the three dots (···) at the top right and toggle **Notifications** to get updates whenever recitations are added or refreshed.

## Dataset

Mushaf configs contain one row per ayah. Each row includes embedded ayah audio, recited Uthmani text, word timestamps, letter timestamps, waqf-aware segments, and source-audio mappings.

The `mushafs` subset is the dataset catalog index. It has one row per published mushaf, joined with reciter names, riwayah/style/channel labels, audio metadata, and coverage.

Each remaining subset is one published mushaf — a config named after the mushaf slug, with a single `train` split. Riwayah, style, and channel for each mushaf live in the `mushafs` catalog.

## Mushaf Schema

| Column | Type | Notes |
|---|---|---|
| `audio` | `Audio` | Ayah MP3 clip. |
| `surah` | `int32` | Surah number, 1-114. |
| `ayah` | `int32` | Ayah number within the surah. |
| `duration_ms` | `int32` | Clip duration. |
| `text_uthmani` | `string` | Exact recited DigitalKhatt V2 text. |
| `segments` | `[[int,int,int,int]]` | Waqf/pause-aware regions: `[word_from, word_to, start_ms, end_ms]`. |
| `word_timestamps` | `[[int,int,int]]` | `[word_idx, start_ms, end_ms]`; word indices are 1-based. |
| `source_url` | `string` | Original chapter or ayah audio URL. |
| `source_offset_ms` | `int32` | Clip start inside `source_url`. |

All row timestamps are relative to the ayah clip. Use `source_offset_ms + timestamp_ms` when mapping a row back to its source audio.

## Catalog Schema

One row per published mushaf.

| Column | Type | Notes |
|---|---|---|
| `slug` | `string` | Stable mushaf identifier (also the subset/config name). |
| `reciter_id` | `string` | Stable reciter identifier (shared across that reciter's mushafs). |
| `name_en`, `name_ar` | `string` | Reciter display name (English / Arabic). |
| `country` | `string` | Reciter country, ISO 3166-1 alpha-2 (e.g. `SA`). |
| `riwayah` | `string` | Riwayah name (e.g. `Hafs`). |
| `style` | `string` | Recitation style (e.g. `Murattal`, `Mujawwad`). |
| `recording_context` | `string` | Recitation context (e.g. `Studio`, `Taraweeh`). |
| `recording_year` | `int32` | Year recorded, when known. |
| `channel` | `string` | Distribution channel name. |
| `audio_category` | `string` | How source audio is segmented: `by_surah` \| `by_ayah`. |
| `chapter_count` | `int32` | Number of chapters in the mushaf. |
| `codec` | `string` | Audio codec. |
| `container` | `string` | File container. |
| `sample_rate_hz` | `int32` | Sample rate. |
| `channels` | `int32` | `1` = mono, `2` = stereo. |
| `bitrate_mode` | `string` | `cbr` \| `vbr` \| `mixed` \| `unknown`. `mixed` = chapters differ. |
| `bitrate_kbps_nominal` | `int32` | Nominal bitrate; `null` when `bitrate_mode` is `mixed`. |
| `total_duration_hours` | `float32` | Total mushaf audio duration, in hours (1 dp). |
| `published_at` | `string` | When the mushaf first entered the dataset, ISO-8601. |
| `updated_at` | `string` | When the mushaf was last published/refreshed, ISO-8601. |

## Usage

The dataset is gated: accept the license on this page, then authenticate (`hf auth login`, or pass `token=`).

```python
from datasets import load_dataset

ds = load_dataset(
    "QUD-Technologies/quranic-universal-ayahs",
    "khalifa_al_tunaiji_tarteel",
    split="train",
)

row = ds[0]
print(row["surah"], row["ayah"])
print(row["word_timestamps"])
print(row["segments"])
```

```python
catalog = load_dataset(
    "QUD-Technologies/quranic-universal-ayahs",
    "mushafs",
    split="all",
)

print(catalog[0]["name_en"], catalog[0]["riwayah"])
```

## Notes

- Best for quick access to verse audio and timestamps together, ayah-by-ayah playback, and ML research.
- Also see [GitHub Releases](https://github.com/QUD-Technologies/quranic-universal-audio/releases/latest), a parallel format using JSON files with versioning and checksums, suitable for offline usage.
- Recitation audio, Qur'an text editions and fonts are not relicensed, and remain under their own terms (see NOTICE).

## License

[QUA Dataset License 1.0](https://github.com/QUD-Technologies/quranic-universal-audio/blob/main/LICENSE-DATA) (`qua-dataset-1.0`), by [QUD Technologies](https://qud.dev).

In short: use it freely, including in commercial products. Any app or feature using this data, or a model trained, fine-tuned, distilled, evaluated or benchmarked on it (or on such a model's output), must be free for every user and fully accessible - not behind, restricted to, or enhanced in a paid tier - in an app that is itself free and shows no advertising; other, unrelated features may be paid. Don't sell the data. Share derivatives under the same license. Credit QUD Technologies - Qur'anic Universal Audio. The full license text governs.
