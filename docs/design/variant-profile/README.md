# Readings profile — Timestamps tab

A public view of how the selected recitation reads wherever Hafs allows more than one way. It shows
what the reciter reads and nothing about how that was decided.

Prototype: `inspector/frontend/src/tabs/timestamps/components/ReadingProfile.svelte`, built on
`domain/reading-choices.ts` (catalogue and labels), `utils/reading-profile.ts` (shard → profile) and
`services/reading-profile-source.ts` (loader). Screenshots in this folder are real local captures
against prod data (`launch.py --mode prod`, read-only).

## Placement and form

- **Trigger:** a double-height footer button right of the recitation picker chip, the same height
  and surface as the shuffle button. It has a fork glyph (two ways to read), with the label
  "Readings" / "الأوجه" under it on screens wider than 1280px and the glyph alone below that. It
  shows only for Hafs deliveries.
- **Panel:** a drop-up anchored to the trigger, like the picker drop-up: `panel` surface, 8px radius,
  `--shadow-pop`, width up to 520px, height up to 78vh, scrolling inside. On screens 640px and
  narrower it becomes a fixed sheet with 16px gutters above the player.
- **Why a drop-up rather than inline:** the Timestamps view is meant to stay calm (PRODUCT.md,
  principle 2). The profile is reference material that belongs to the recitation, not to the
  current verse. It sits next to the control that picks the recitation and stays out of the frame
  until someone opens it. An inline element would add permanent chrome to every verse.

## Content

Header: "Readings" / "أوجه القراءة", then the reciter's Latin and Arabic names, then one intro line.

Groups are listed in this order and appear only when they have rows: Hamza → Letters and vowels →
Joining words → Brief pause → Stopping and starting.

Inside a group, each **family** (selectors that share one explanation) has a title, a plain-language
description, and one **word row** per word:

- **Word:** the reading's own word text from the shard, set in `--font-quran` (DigitalKhatt). A
  boundary choice shows both words.
- **Options:** every option in the family's order. An option the reciter reads gets a filled accent
  check, a bold label and one accent `s:v` chip per verse. An option they never read gets an empty
  ring and the muted text "Other reading" / "وجه آخر". In English the Arabic term also appears next
  to the label (Ibdal إبدال).
- When occurrences differ, both options are marked as read, each with its own verses. For example,
  Qatami reads ءَآللَّهُ as ibdal at 27:59 and as tashil at 10:59.

| Family | Selectors | Group | Options |
|---|---|---|---|
| Question hamza before al- / همزة الاستفهام قبل «ال» | istifham_article (one row per word: ءَآلذَّكَرَيْنِ, ءَآلْـَٔـٰنَ, ءَآللَّهُ) | Hamza | ibdal, tashil |
| Seen or ṣād / السين أو الصاد | yabsut, bastah, almusaytirun, bimusaytir | Letters | seen, saad |
| Fatha or damma / الفتح أو الضم | daaf_haraka | Letters | fatha, damma |
| Nūn and Yā-Sīn, joined on / «ن» و«يس» عند الوصل | noon_wasl, yaseen_wasl | Joining | izhar, idgham |
| Merging into the next word / الإدغام في الكلمة التالية | irkab_maana, yalhath_dhalik | Joining | idgham, izhar |
| Pause between words / السكت بين الكلمتين | iwaja_qayyima, man_raq, bal_ran | Brief pause | sakt, idraj |
| Māliyah, halaka / «ماليه هلك» | maliyah_halak | Brief pause | sakt, idgham |
| Last letter when stopping / الحرف الأخير عند الوقف | yaa_aatani_waqf, salasila_waqf | Stopping | ithbat, hadhf |
| Starting from al-ism / الابتداء بـ«الاسم» | alism_ibtidaa | Stopping | hamza, lam |

The descriptions are in `messages/{en,ar}.json` under `ts_readings_*_desc`.

### Option labels

| id | en | ar |
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

The per-row hover tooltip (`variantTipLines`) uses the same family title and option label in
place of the old title-cased id. This change is implemented.

## Behaviour rules

- **Shown selectors only.** A selector absent from `CHOICES` is never shown. This covers the nasal
  places, all `raa_*` selectors and `tamanna_noon`.
- **Conditional rows come from the data.** The producer writes a variant on a reading only when
  that reading met the occurrence's condition: it stopped there (waqf), joined there (wasl or sakt
  boundary), or started there (ibtidaa). The profile lists only what is present, so those rows
  appear only when they applied. For example, Fatih has no `yaseen_wasl` row because he stopped
  after Yā-Sīn, and neither reciter has a `noon_wasl` or waqf row.
- **Picks without evidence (`by: "default"`) are dropped**, so the panel never claims a reading
  the reciter was not shown to make. Neither reciter has one today.
- **Deduped and ordered:** repeated readings of the same verse fold into one chip. Chips,
  families and word rows follow mushaf order. An occurrence that crosses a verse end is labelled
  `36:1–2` and links to its first verse.
- **Jump:** a chip sets `pendingTsNavigation` with `{surah, ayah, autoplay: true, slug}`, which is
  the same channel the Bookmarks panel and flag notifications use. The Timestamps tab then routes it
  through `jumpToTarget`, and the panel closes. This was verified live: clicking 10:59 loaded and
  played 10:59 for Qatami.
- **States:**
  - Loading shows "Gathering readings… n of 17 surahs" with the shared spinner.
  - An error shows a message and a "Try again" button.
  - A recitation without v15 shards shows the empty line "No recorded reading choices yet".
- **Keyboard and accessibility:**
  - The trigger is a `button` with `aria-expanded` and `aria-controls`, and opening the panel
    moves focus into it.
  - The panel is a non-modal `role="dialog"`. Escape closes it and returns focus to the trigger,
    and a click outside closes it.
  - Chips are buttons with an accent focus ring and an `aria-label` such as "Go to Yunus 10:59".
- **RTL:** the layout uses logical properties, so in Arabic the word sits on the right and the
  options on the left. Chips are `dir="ltr"` so `10:59` never flips, and digits stay Western.
- **Themes:** the panel uses tokens only, so light and dark both follow `theming.md`.

## Data path

**Prototype (client-side, real data):** on first open, the client fetches the reciter's raw shards
through `shard_url_template`. It reads only the 17 chapters that can hold a shown selector
(`CHOICE_CHAPTERS`), 4 at a time, keeps only the variant hits and drops each shard. If the first
shard is older than v15, it stops early. The result is cached per delivery for the session. The
cost is about 17 × 2 MB of JSON. That is fine against the CDN but slow against the local prod
bucket: about 9 s once the backend is warm, and minutes when cold.

**Production (proposed):** the shard producer (`qua_sdk.integrations.shard_variants`) should also
write a per-delivery `reciters/<slug>/timestamps/readings.json`. It would hold the shown
occurrences `{id, chosen, words: ["s:v:w"], texts: [...]}`, already filtered by the same rules
(shown, active, not `default`). The Inspector would serve it at `GET /api/ts/readings/<slug>`,
which is a few KB and cacheable. The client would then call `buildProfile` on it unchanged, and the
trigger could hide for deliveries without the file instead of showing an empty state.

## Open questions

1. Should a `by: "default"` pick appear (for example as "not determined"), or stay hidden as now?
2. "Readings" / "الأوجه" as the public name: is "Reading choices" clearer to non-specialists?
3. Should `daaf_haraka` show its three occurrences as one chip (30:54, the current behaviour) or
   one chip per word?
4. The descriptions need review by a qualified teacher, especially the Arabic.
5. The app shell itself overflows at phone width (header tabs and footer). The panel copes, but the
   footer at 390px is crowded.
6. Jumping currently autoplays, matching bookmarks. Should it keep the current play state instead?
