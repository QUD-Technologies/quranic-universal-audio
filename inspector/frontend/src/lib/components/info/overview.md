# About Qur'anic Universal Audio

## Overview

Qur'anic Universal Audio aims to automatically generate accurate verse-, word- and letter-level timestamps for full Qur'an recitations at scale, with a human-in-the-loop review system that robustly catches any potential errors.

Doing that well depends on a second goal: unifying recitations in one place. We collect reciters, normalize their metadata, and bring together their different audio, styles, riwayahs, sources/channels, CDNs and versions under a single catalog. From there, anyone can contribute — submit a new recitation, edit a reciter's metadata, or review the generated alignment.

## Use Cases

- **Verse playback** — play or seek any ayah or ayah range straight from the original surah audio.
- **Follow-along** — word-by-word highlighting synced to the recitation.
- **Word study** — replay the sound of individual words for learners.
- **Tajweed research** — measure ghunnah and madd durations from letter timestamps, study cross-word effects and silent-letter interactions, and support tajweed teaching.
- **ML research** — a large, diverse corpus (reciters, paces, styles, riwayat) for speech recognition, tajweed, recitation start/stop detection, and reciter identification.

## How it Works

Each mushaf is run through our AI pipeline, automatically split into segments at the reciter's pauses and matched to the Qur'anic text. Since the algorithms and AI are not always perfect, and for the sanctity of the Qur'an, we do a lot of post-processing and verification to catch and flag possible issues and low confidence segments. After getting familiar with the editing guide, a reviewer then listens through them and corrects any errors.

Only once the segments are reviewed, timestamps are generated and published — doing that last is what keeps them accurate. Any issues discovered after publication are simply fixed in the segments as usual, and timestamps are refreshed, which is what makes the pipeline iterative and self-correcting.

## Lifecycle

Every recitation carries a status that shows where it is on that path:

::lifecycle
- available_for_request: We have the audio ready, but no one has requested alignment yet.
- requested: Alignment has been requested; the pipeline is processing the audio.
- available_for_review: Initial alignment completed; awaiting someone to review the errors. You can start a review by clicking the 'Claim Review' button
- under_review: A reviewer has claimed it and is correcting the segments.
- published: Reviewed, timestamped, and live for anyone to use the data.

A recitation is a combination of reciter x riwayah x style x recording context x channel x version/year, so the same reciter can have multiple recitations. Both full and partial mushaf recitations are available, although full mushaf recitations are preferred.

You can also submit your own custom reciters and audio links.

## Tabs

- **Dashboard** — the front door. Browse and search reciters, listen to audio, follow recent activity, and submit new recitations.
- **Timestamps** — the output. For published reciters, play the recitation with word- and letter-level highlighting alongside the waveform and do granular timestamps analysis.
- **Segments** — where reviewing happens, the middle of the pipeline. Segments are edited and fixed here first before timestamps are generated. 

## Contributing 

Anyone is welcome to contribute — you just need to login with an account. The main ways to contribute are:

- **Requesting/submitting recitations:** review audio and metadata of catalogued recitations, or submit your own and contribute to the catalog and dataset. We typically process recitations within a few days.

- **Reviewing segments:** claim recitations that are available for review and fix any flagged errors in the segments. Anyone can review, as long as you are proficient in reading and understanding recitations. A typical recitation might take 30 - 120 minutes for a full review.

- **Reviewing timestamps:** while there is no formal way to edit timestamps directly, you can report any general issues that might be present for published recitations, and we'll try our best to resolve them.

- **General feedback:** bug reports, feature requests, suggestions, and collaborations are welcome. Feel free to [open an issue on GitHub](https://github.com/QUD-Technologies/quranic-universal-audio/issues) or reach out directly.

- **Supporting the project:** like, star, post, share the data and spread the project.

## Data Access

Audio and timestamps are published in two open formats, pick by your use case:

- [GitHub Releases](https://github.com/QUD-Technologies/quranic-universal-audio/releases) — JSON files per recitation, in verse, word and letter tiers, paired with the original chapter audio by URL; best for apps and offline use. Audio isn't bundled — you stream it from the source links. Released shortly after timestamps are generated or refreshed.
- [Hugging Face Dataset](https://huggingface.co/datasets/QUD-Technologies/quranic-universal-ayahs) — the same timestamps in parquet, one row per ayah with the ayah's audio embedded. Best for ML and analysis: query and filter rows, and get audio plus timestamps together.

Both formats support both gapless surah and ayah-by-ayah playback. Both ship a single take per ayah (the first occurrence), so in rare cases where a reciter repeats an ayah fully or partially at the ayah start/end, follow-along highlighting may pause until they move past the repetition (within-ayah repetitions are still preserved). A unified API — which also exposes the full, unfiltered duplicates — is on the roadmap.

## Privacy

When you sign in with Hugging Face, we store your Hugging Face user ID and username, and we record the edits, reviews, and actions you make for contribution history. We don't collect your email, no third-party tracking or ads, and do not share your data. Data is hosted on Hugging Face infra, whose terms also apply. Your contributions are visible to maintainers only and not to the public.
