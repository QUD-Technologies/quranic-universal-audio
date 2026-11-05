# Mushaf view

The Timestamps tab's alternative surface: the recitation read along in a two-page Madani mushaf, drawn by `@qud-technologies/mushaf-svelte` (the `qud-mushaf` packages, vendored under `inspector/frontend/vendor/`) instead of the waveform + analysis grid. Hafs only (the only edition with page layouts). Word-level highlighting only.

## What it shows

- **Layout.** Right (odd) page + left (even) page; one page when the window can't fit two at a useful size (the package's `mode: 'auto'`: two pages only at ≥ 0.8 × the one-page font or ≥ 24px). No manual single/double switch.
- **Page chrome.** The package's default drawing: header = surah-name ligature · `Juz N · Hizb N`; footer = page number; framed surah headers (`U+E000` stretched to the column + the `surahNNN` ligature); basmala lines with font feature `basm` at 0.75×; pages 1–2 centred vertically. Labels are the app's (`ts_mushaf_juz` / `_hizb` / `_page_aria` / `_next` / `_prev`), digits Arabic-Indic in Arabic. Without the surah-name font the name is plain `سورة <name>`; without the frame font a ruled box.
- **Highlight.** Package highlights built by `highlights.ts::readingHighlights` from the position and the prefs: the recited word `text` (accent colour; the footer droplet recolours it via `accentVarText`), the current verse `band` (one rectangle per line; only its visible words), `hidden` for the visibility prefs, plus two consumer tokens styled through `::part(hl-…)`: `pick` (Repeat spotlight) and `inert`. `setHighlights` is the package's fast path; the view only calls it when the list changes. Waṣl across verses moves the band verse by verse. Nothing lights in silence; verse markers never light.
- **Loop-backs.** Position comes from every take of every word (`position.ts`, reusing `recitation-active.ts`), so when the reciter goes back one or more verses the highlight and the page go back too. In silence the position holds on the word recited last in *time*.
- **Follow.** The book turns when playback reaches the next spread (`element.next()`, the package's slide; instant under reduced motion); a jump (seek, first load) cuts straight there (`goTo(anchor)`). Paging by hand (◀ ▶ beside the book in the package's 44px slots, ← →; ← = forward, the book is RTL) stops following and shows **Back to reciting**; any seek, or paging back onto the recited spread, resumes following. A `pageChange` the view did not ask for is the reader's. Spreads with no surah the reciter has are skipped (`canNavigate`).
- **Chapter / reciter switch.** The book turns to the new chapter's page at once (`goTo` the pending seek's word, else verse 1), and the audio is **held** (`dashPort.pause`, re-asserted on `onPlay` and per frame while the load is in flight) until the chapter's timings are indexed, then resumed — playback never runs ahead of its page. Only a switch holds (opening the view on a playing chapter just follows it); a running Repeat drives its own playback. The index is keyed `slug:chapter`, so a reciter switch on the same surah never reads the old reciter's timings.
- **Clicks.** The package's `wordTap` seeks a word to its first occurrence; `ayahTap` (a verse marker) seeks to the verse start. A word in another surah the reciter has switches chapter, then seeks. Words of surahs the reciter lacks are inert.
- **Reading on.** At chapter end (not repeating) playback continues into the reciter's next `ts_chapters` entry.

## Settings (`stores/mushaf.ts`, localStorage)

| Key | Values | Effect |
|---|---|---|
| `ts_mushaf_mode` | bool | view on/off (`M`, or the book icon in the footer) |
| `ts_mushaf_year` | `1405` / `1421` / `1441` | print layout; 1405 renders in DK v1, 1421 + 1441 in DK v2 |
| `ts_mushaf_scope` | `spread` / `verse` | hide every verse but the current one |
| `ts_mushaf_show_upcoming` | bool | hide words after the last one reached |

**Full screen** (footer ⤢ button, `F` or `F11`; the window going full screen any other way — OS/browser full screen, detected via `(display-mode: fullscreen)` or window = screen size — enters the same mode without the Fullscreen API and leaving it exits; exiting the mode while the window stays full screen suppresses re-entry until it leaves, and `F11` out of an OS full screen is left to the browser; `Esc` / `F` / leaving the tab or view exits; `fullscreen.svelte.ts`, not persisted): `html.mushaf-full` hides the app header and the browser is asked for real full screen. If it refuses (embedded pane, iframe policy) the chrome still hides and the book scales to the window. After 1.2 s the footer **docks** (`html.mushaf-dock`: slides off the bottom, the book takes its height bar a 22 px strip). The whole 22 px bottom strip is the hover target (a chevron pill marks its centre) and brings it back overlaid on the book (`html.mushaf-dock-open`) on hover/focus/click; it slides away 1.2 s after the pointer leaves, unless something in it is in use (hovered, focused, a drop-up open, a Repeat verse being picked). The view re-measures on toggle and on dock since the book's edges move without a resize.

`verse` + hide upcoming = memorisation mode (only what has been recited shows). `mushafActive = mushafMode && isHafs(playerContext.delivery.riwayah)`.

## Repeat (`repeat.svelte.ts`, `repeat-plan.ts`)

Mushaf-only. A verse range (`From` → `To`, `To` empty = one verse; typed as `s:a`, picked by clicking a verse on the page, or filled by *This verse / This page / This surah*), **each verse × N**, **whole range × M** (1–10 or ∞) and **Pause between** (None default, 0.5–10 s, `REPEAT_PAUSES_MS`; changeable mid-run). Ranges may span surahs; surahs the reciter lacks are skipped. `To` never precedes `From`: a typed one is flagged invalid, and while picking `To` the verses before `From` neither light up nor accept the click (a new `From` past the old `To` collapses the range to that verse). Picking dims the page and lights the verse under the pointer — or the range it would make — and reveals every verse whatever the visibility prefs.

- Plays each verse's **canonical take** (`canonical.ts`, a TS port of `qua_shared/timestamps_native.py::_canonical` over word occurrences: split on foreign-verse interleaving, first completing occasion, cut at completion, drop a leading false start) — never the reciter's re-dos.
- Pause **None**: continuous — consecutive verses whose takes are adjacent in the recording play straight on, replays and other jumps seek at once. A set pause puts that silence between **every** two plays: replays of a verse and the step from the last play of verse N to the first of N+1 alike.
- Driven by `MushafView`'s rAF + the port's `timeupdate`. A seek the driver didn't make (footer skip, scrub, word click) ends the repeat. Cross-surah steps ask the view to switch chapter, then resume once that chapter is live.

## Sizing

The package's (`@qud-technologies/mushaf-core` `fit`, ported from QUA's `fit.ts` and checked bit for bit): natural word spacing, no kashida justification; a column sized to the 95th-percentile line; the base font = min(height fit at pitch 1.75, width fit for 1 or 2 pages); a dense spread shrinks its own font. The view gives the element its box: full width, height from the tab's top to the shell player's real top edge (not `--player-h`, which omits its progress rail; in docked full screen the window bar the 22px handle strip). The package's default gutters are QUA's (top 0, bottom 12, 8 inline, 44px nav slots, 2px spine).

## Data + assets

- **Data:** the Talqeen pack (`quran-core.qref`) through the TS SDK (`@qud-technologies/quran-data`, installed from its release tarball): page layouts of 1405/1421/1441, Digital Khatt v2 words, ayah-end markers, the basmala, and each layout's stop edition (1405 has its own; 1421 + 1441 the default). `lib/quran-data/pack.ts` opens it once from `/api/static/quran-data/quran-core.qref`.
- **Fonts:** the pack's `fonts()` (DK v2 for 1421/1441, DK v1 for 1405, surah-name v2, the QuranCommon surah frame), served by `/api/static/quran-data/<file>` (`fontUrl`). The package falls back to another text font when a layout's own one fails, measuring and drawing in the same face, and to plain text / a ruled box for the header fonts. App-wide, `styles/base.css`'s `DigitalKhatt` face is the same DK v2 file from the same route.

## Footer + shell in Mushaf view

- Hidden: the waveform, analysis row, owner validation panel, the shared NowReciting bar (`lib/stores/now-reciting-suppressed.ts`), shuffle (button hidden, auto-shuffle unarmed, `R` ignored).
- Also hidden (not greyed — they have nothing to act on): loop word, letters, phonemes, wipe, tajweed, the shortcuts help, report.
- Added: book toggle, Repeat drop-up, Mushaf settings drop-up, full screen (`mushaf/MushafFooter.svelte`, first in the analysis cluster).

## Files

`tabs/timestamps/mushaf/`: `MushafView` (the `<QudMushaf>` host: data, box, follow, clicks, keys, repeat host), `highlights.ts` (reading state → package highlights), `position.ts` (time → word anchor), `MushafFooter` / `MushafSettings` / `MushafRepeatPanel`, `fullscreen.svelte.ts`, `canonical.ts`, `repeat-plan.ts`, `repeat.svelte.ts`. Tests in `__tests__/`. Look parity before/after a change: `scripts/diagnostics/mushaf_look_parity.mjs`.
