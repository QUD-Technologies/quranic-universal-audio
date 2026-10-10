# Readings panel — Timestamps tab

A public view of how the selected recitation reads wherever Hafs allows more than one way. It shows
what the reciter reads, never how that was decided.

| Piece | Where |
|---|---|
| Panel | `inspector/frontend/src/tabs/timestamps/components/ReadingProfile.svelte`, docked by `TimestampsFooterLeft.svelte` |
| Layout and labels | `tabs/timestamps/domain/reading-choices.ts`, `utils/reading-profile.ts`, `services/reading-profile-source.ts` |
| Summary builder | `inspector/services/reference/readings.py` |
| Stored summary | `reciters/<slug>/readings.json`; schema `qua_shared/schemas/bucket/ts_readings.py` (`TsReadingsDoc`, codegen'd to `schemas.ts`) |
| Route | `GET /api/ts/readings/<slug>` in `inspector/routes/timestamps/timestamps.py` |

Screenshots in this folder are local captures of the real stack against prod data, read-only
(`launch.py --mode prod`).

## Placement

The panel is a side panel docked to the open recitation picker at the picker's height
(`min(600px, 74vh)`). No extra button opens it: opening the picker shows it whenever the selected
recitation is Hafs, and for any other riwayah nothing is rendered. Overflow scrolls inside the panel.

At 760px and narrower, the picker and the panel stack in one sheet with 16px gutters above the player.
The list takes the top half and the panel the bottom half, and the page gets no horizontal scroll.

Read as a strip in the footer beside the picker chip, at the chip's height, the panel was tried
first and rejected. At 1440px the footer's left zone leaves about 150px beside the chip, enough for
one word. The rows also pushed the Report and surah controls into the transport.

## Content

- The panel has a heading ("Readings" / "أوجه القراءة").
- Group headings come in this order: Hamza, Letters and vowels, Joining words, Brief pause, Stopping
  and starting. A group appears only when it has rows.
- Each group has one row per word. The row shows the word in `--font-quran` (DigitalKhatt) beside its
  options, in the family's order.
  - An option the reciter reads gets a filled accent check, a bold label and its verse chips.
  - An option the reciter never reads is a dimmed ring and label, with no caption.
- When occurrences differ, both options are marked. For example, Qatami reads ءَآللَّهُ as ibdal at
  27:59 and as tashil at 10:59.
- There are no descriptions, intro or "Other reading" captions.
- Labels follow the UI locale only and are never bilingual.

**Verse chips** show `s:v`, or `s:v1–v2` when the words cross a verse end. Each chip carries a small
go-to arrow pointing up and onward. The arrow mirrors in RTL. On hover or focus the chip fills, its
border turns accent and the arrow nudges outward. The accessible name is "Go to Yunus 10:59" /
"انتقل إلى يونس 10:59".

Clicking a chip sets `pendingTsNavigation {surah, ayah, autoplay: true, slug}`, the channel the
Bookmarks panel uses, and closes the picker. In the live capture, clicking 10:59 loaded and played
10:59 for Qatami (`qatami-en-dark-after-jump-10-59.png`).

**Per-row hover** (`variantTipLines`) shows the family title and option label in the UI locale,
then "This recitation" / "Other reading". The producer's English description line is gone.

| Group | Family title (hover) | Selectors | Options |
|---|---|---|---|
| Hamza / الهمز | Question hamza before al- / همزة الاستفهام قبل «ال» | istifham_article, one row per word | ibdal, tashil |
| Letters and vowels / الحروف والحركات | Seen or ṣād / السين أو الصاد | yabsut, bastah, almusaytirun, bimusaytir | seen, saad |
|  | Fatha or damma / الفتح أو الضم | daaf_haraka | fatha, damma |
| Joining words / الوصل والإدغام | Nūn and Yā-Sīn, joined on / «ن» و«يس» عند الوصل | noon_wasl, yaseen_wasl | izhar, idgham |
|  | Merging into the next word / الإدغام في الكلمة التالية | irkab_maana, yalhath_dhalik | idgham, izhar |
| Brief pause / السكت | Pause between words / السكت بين الكلمتين | iwaja_qayyima, man_raq, bal_ran | sakt, idraj |
|  | Māliyah, halaka / «ماليه هلك» | maliyah_halak | sakt, idgham |
| Stopping and starting / الوقف والابتداء | Last letter when stopping / الحرف الأخير عند الوقف | yaa_aatani_waqf, salasila_waqf | ithbat, hadhf |
|  | Starting from al-ism / الابتداء بـ«الاسم» | alism_ibtidaa | hamza, lam |

| Option | en | ar |
|---|---|---|
| ibdal | Ibdal | إبدال |
| tashil | Tashil | تسهيل |
| fatha | Fatha | فتح |
| damma | Damma | ضم |
| seen | Seen | سين |
| saad | Saad | صاد |
| izhar | Izhar | إظهار |
| idgham | Idgham | إدغام |
| sakt | Sakt | سكت |
| idraj | Idraj | إدراج |
| ithbat | Ithbat | إثبات |
| hadhf | Hadhf | حذف |
| hamza | Hamza | همزة |
| lam | Lam | لام |

## Behaviour rules (server builder)

- **Shown selectors only** (`readings.SHOWN`). Never shown: the nasal places, the `raa_*`
  selectors and `tamanna_noon`.
- **Conditional rows come from the data.** A shard carries a variant only where its condition held:
  stopped there (waqf), joined there (wasl or a sakt boundary), or started there (ibtidaa). So those
  rows appear only where they applied. Fatih has no `yaseen_wasl` row because he stopped after Yā-Sīn.
- **`by: "default"` picks are dropped:** without evidence they say nothing about the reciter.
- **One row per selector and word.** `istifham_article` splits into ءَآلذَّكَرَيْنِ, ءَآلْـَٔـٰنَ and
  ءَآللَّهُ. Rows follow mushaf order; repeated readings of a verse fold into one chip.
- **Non-Hafs, pre-v15:** a non-Hafs delivery gets empty `rows` with no shard read. Pre-v15 shards
  contribute nothing.

## Data path

`readings.build(slug)` reads the 17 chapters that can hold a shown selector, through
`data_dir.read_timestamps_chapter`, and folds them into `TsReadingsDoc`. The summary is written in
three situations:

- **Online (aligner timestamps run):** `ts_aligner_runner._run` rebuilds it after the run's shards
  are written, whatever the outcome, before closing the run record.
- **Offline (Katana publish):** the `ts-refreshed` internal notice rebuilds it on a background
  thread.
- **Lazily:** `GET /api/ts/readings/<slug>` builds and stores it when it is missing or invalid.

A read-only backend (prod mode locally) cannot store it, so it keeps the built summary in process
memory instead. The route has the same visibility gate as `/shard` (`is_viewable`) and returns
`no-store`. The client makes one request per delivery and caches it for the session; there is no
shard fanout.

**Path choice:** `readings.json` sits at the reciter root next to `ts_validation.json`, not in
`timestamps/`. Bucket tooling (`scripts/bucket/bucket_reciters.py`, `upload_bucket_reciter.py`)
treats every file in `timestamps/` as a shard.

**Cost:** a cold first build reads 17 shards. Against the prod bucket over hffs locally that took
about 108 s. Afterwards the stored file serves in milliseconds.

## Open questions

1. **Panel placement:** I read "next to the recitation picker, the same height as the picker" as
   docking beside the picker drop-up, so the panel shows while the picker is open. If it should stay
   on screen with the picker closed, the footer has no room at 1440px. It would need its own place,
   such as a column of the Timestamps view.
2. **Backfill:** existing v15 reciters get their summary on the first request (about 108 s against
   the prod bucket). A one-off `refresh` per released Hafs reciter after deploy would avoid that wait.
3. Should a `by: "default"` pick appear at all?
4. The Arabic wording needs review by a qualified teacher.
