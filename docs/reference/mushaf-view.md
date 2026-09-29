# Mushaf view

The Timestamps tab's alternative surface: the recitation read along in a two-page Madani mushaf instead of the waveform + analysis grid. Hafs only (the only edition with page layouts). Word-level highlighting only.

## What it shows

- **Layout.** Right (odd) page + left (even) page; one page when the window can't fit two at a useful size (`fit.ts::fitPages`, `SPREAD_MIN_RATIO` / `SPREAD_MIN_FONT_PX`). No manual single/double switch.
- **Page chrome.** Header = surah-name ligature · `Juz N · Hizb N` (no rule under it); footer = page number. Rows are `CHROME_ROW_PITCH` 0.7 line tall, page block padding `PAD_BLOCK_EM` 0.55. If the surah-name font fails to load the name renders as plain Arabic text (`سورة <name>` from `surah-info`), and without the frame font a quiet ruled box stands in — never the raw `surahNNN` ligature key. Surah headers use the frame glyph (`U+E000`, stretched to the column) + the `surahNNN` ligature; basmallah lines render DK words 1–4 with `font-feature-settings: 'basm'`. Pages 1–2 (8 lines) are centred vertically.
- **Highlight.** The recited word in the accent colour (the footer droplet recolours it via `accentVarText`), a faint band behind the *current verse only* — per line, positioned imperatively. Waṣl across verses moves the band verse by verse. Nothing lights in silence; verse markers never light.
- **Loop-backs.** Position comes from every take of every word (`position.ts`, reusing `recitation-active.ts`), so when the reciter goes back one or more verses the highlight and the page go back too. In silence the position holds on the word recited last in *time*.
- **Follow.** The book turns when playback reaches the next spread (3D leaf, 620 ms; instant under reduced motion; a timer settles it when `animationend` can't fire in a hidden tab). Reading on turns one leaf; a jump (seek, first load) cuts straight to the page. Paging by hand (◀ ▶ flanking the book, ← →; ← = forward, the book is RTL) stops following and shows **Back to reciting**; any seek, or paging back onto the recited spread, resumes following. Spreads with no surah the reciter has are skipped.
- **Chapter / reciter switch.** The book turns to the new chapter's page at once from the layout alone (the pending seek's word, else verse 1), and the audio is **held** (`dashPort.pause`, re-asserted on `onPlay` and per frame while the load is in flight) until the chapter's timings are indexed, then resumed — playback never runs ahead of its page. Only a switch holds (opening the view on a playing chapter just follows it); a running Repeat drives its own playback. The index is keyed `slug:chapter`, so a reciter switch on the same surah never reads the old reciter's timings.
- **Clicks.** A word seeks to its first occurrence; a verse marker seeks to the verse start. A word in another surah the reciter has switches chapter, then seeks. Words of surahs the reciter lacks are inert.
- **Reading on.** At chapter end (not repeating) playback continues into the reciter's next `ts_chapters` entry.

## Settings (`stores/mushaf.ts`, localStorage)

| Key | Values | Effect |
|---|---|---|
| `ts_mushaf_mode` | bool | view on/off (`M`, or the book icon in the footer) |
| `ts_mushaf_year` | `1405` / `1421` / `1441` | print layout; 1405 renders in DK v1, 1421 + 1441 in DK v2 |
| `ts_mushaf_scope` | `spread` / `verse` | hide every verse but the current one |
| `ts_mushaf_show_upcoming` | bool | hide words after the last one reached |

**Full screen** (footer ⤢ button or `F`; `Esc` / `F` / leaving the tab or view exits; `fullscreen.svelte.ts`, not persisted): `html.mushaf-full` hides the app header and the browser is asked for real full screen. If it refuses (embedded pane, iframe policy) the chrome still hides and the book scales to the window. After 1.2 s the footer **docks** (`html.mushaf-dock`: slides off the bottom, the book takes its height bar a 22 px strip). A small chevron handle pinned bottom-centre (same chip as the NowReciting collapse) brings it back overlaid on the book (`html.mushaf-dock-open`) on hover/focus/click; it slides away 1.2 s after the pointer leaves, unless something in it is in use (hovered, focused, a drop-up open, a Repeat verse being picked). The view re-measures on toggle and on dock since the book's edges move without a resize.

`verse` + hide upcoming = memorisation mode (only what has been recited shows). `mushafActive = mushafMode && isHafs(playerContext.delivery.riwayah)`.

## Repeat (`repeat.svelte.ts`, `repeat-plan.ts`)

Mushaf-only. A verse range (`From` → `To`, `To` empty = one verse; typed as `s:a`, picked by clicking a verse on the page, or filled by *This verse / This page / This surah*), **each verse × N**, **whole range × M** (1–10 or ∞) and **Pause between** (None default, 0.5–10 s, `REPEAT_PAUSES_MS`; changeable mid-run). Ranges may span surahs; surahs the reciter lacks are skipped. `To` never precedes `From`: a typed one is flagged invalid, and while picking `To` the verses before `From` neither light up nor accept the click (a new `From` past the old `To` collapses the range to that verse). Picking dims the page and lights the verse under the pointer — or the range it would make — and reveals every verse whatever the visibility prefs.

- Plays each verse's **canonical take** (`canonical.ts`, a TS port of `qua_shared/timestamps_native.py::_canonical` over word occurrences: split on foreign-verse interleaving, first completing occasion, cut at completion, drop a leading false start) — never the reciter's re-dos.
- Pause **None**: continuous — consecutive verses whose takes are adjacent in the recording play straight on, replays and other jumps seek at once. A set pause puts that silence between **every** two plays: replays of a verse and the step from the last play of verse N to the first of N+1 alike.
- Driven by `MushafView`'s rAF + the port's `timeupdate`. A seek the driver didn't make (footer skip, scrub, word click) ends the repeat. Cross-surah steps ask the view to switch chapter, then resume once that chapter is live.

## Sizing (`fit.ts`, `measure.ts`)

No kashida justification. Natural spacing, minimised stretch:

1. Every word is measured once on a canvas in the print's font (em, cached per font).
2. Column width = the 95th-percentile justified-line width (sampled every 7th page) × font size — constant across spreads, so the page box never jumps.
3. Base font = min(height-fit at `PITCH_MIN` 1.75, width-fit for 1 or 2 pages). Width-bound windows spend spare height on line pitch, capped at `PITCH_MAX` 2.3.
4. Per spread: font = min(base, column ÷ (densest line × (1 + `FIT_SLACK` 1.5%)); the slack absorbs DOM runs landing slightly wider than canvas `measureText`) — only unusually dense spreads shrink. Full lines stretch (`space-between`) by the small remaining slack; `MIN_WORD_GAP_EM` 0.2 is the floor.
5. Viewport height = the shell player's real top edge (not `--player-h`, which omits its progress rail). The book hugs the top (`OUTER_TOP_PX` 0, `OUTER_BOTTOM_PX` 12); the width budget reserves `NAV_SLOT_PX` each side for the ◀ ▶ buttons, which sit beside the book, not at the window edges.
6. The page's `<header>` / `<footer>` zero their margins — the app's global element margins would otherwise push the page-number row off the paper.

## Data + assets

- **Layouts:** `mushaf/data/layout-<year>.json` (~48 KB gz each), lazy-imported JS chunks. Generated by `scripts/codegen/build_mushaf_layouts.py --src <quranic-universal-mushaf>` from the QUL SQLite layouts + juz/hizb metadata. Shape `{year, pages: [[kind, centered, a, b], …], juz, hizb}`; ayah lines carry DK word-id ranges (verse markers included), surah lines the surah number.
- **Text:** the DK v2 script the tab already loads (`loadDk()`, `data/digital_khatt_v2_script.json`) — its ids are the layout's. Indexed once per session (`layout.ts::loadWordIndex`).
- **Fonts:** DK v2 is the inlined `DigitalKhatt` face. If a year's own face fails to load (1405's DK v1 missing from a bucket), the pages *and* the line measurement fall back to DK v2 together — measuring one face while rendering another overflows lines and breaks their justification. `surah-name-v2.woff2`, `juz-font.woff2`, `digital-khatt-madani-v1.woff2` live in the bucket at `reference/mushaf-fonts/` and are served by `/api/static/mushaf-font/<name>` (whitelisted, immutable) — bucket, not `public/`, because HF Spaces ship binaries as LFS pointers. Loaded with the FontFace API on first use (`fonts.ts`).

## Footer + shell in Mushaf view

- Hidden: the waveform, analysis row, owner validation panel, the shared NowReciting bar (`lib/stores/now-reciting-suppressed.ts`), shuffle (button hidden, auto-shuffle unarmed, `R` ignored).
- Also hidden (not greyed — they have nothing to act on): loop word, letters, phonemes, wipe, tajweed, the shortcuts help, report.
- Added: book toggle, Repeat drop-up, Mushaf settings drop-up, full screen (`mushaf/MushafFooter.svelte`, first in the analysis cluster).

## Files

`tabs/timestamps/mushaf/`: `MushafView` (data, sizing, follow, clicks, keys, repeat host), `MushafBook` (slots + turning leaf), `MushafPage` (static page markup), `MushafFooter` / `MushafSettings` / `MushafRepeatPanel`, `fullscreen.svelte.ts`, `layout.ts`, `fit.ts`, `measure.ts`, `position.ts`, `highlight.ts` (imperative classes + verse bands), `canonical.ts`, `repeat-plan.ts`, `repeat.svelte.ts`, `fonts.ts`. Tests in `__tests__/`.
