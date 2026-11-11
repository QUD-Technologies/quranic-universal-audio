# Mushaf view

The Timestamps tab's alternative surface: the recitation read along in a two-page Madani mushaf, drawn by `@qud-technologies/mushaf-svelte` (the `qud-mushaf` packages, vendored under `inspector/frontend/vendor/`) instead of the waveform + analysis grid. Hafs only (the only edition with page layouts).

## What it shows

- **Layout.** Right (odd) page + left (even) page; one page when the window can't fit two at a useful size (the package's `mode: 'auto'`: two pages only at ≥ 0.8 × the one-page font or ≥ 24px). No manual single/double switch.
- **Page chrome.** The package's default drawing: header = surah-name ligature · `Juz N · Hizb N`; footer = page number; framed surah headers as the Mushaf app's (`U+E000` stretched to the column + the `surahNNN` ligature at 0.86, the ayah count and a Makki/Madani icon in the medallions); basmala at 0.78×; tajweed the classic preset with the app's default-off rows (madd tabi'i) plain (`shownColours`); pages 1–2 centred vertically. Labels are the app's (`ts_mushaf_juz` / `_hizb` / `_page_aria` / `_next` / `_prev`), digits Arabic-Indic in Arabic. Without the surah-name font the name is the pack's plain name, which already reads `سُورَةُ …` (no label prefix); without the frame font a ruled box.
- **Highlight.** As the Mushaf app, the page carries no verse band and no faded words: the read-along playhead (below) is the only mark of where the recitation is. `highlights.ts::readingHighlights` gives only `hidden` (the visibility prefs) and `inert` (surahs the reciter lacks, `::part(hl-inert)`). `setHighlights` is the package's fast path; the view only calls it when the list changes. Reading CSS is the app's: by word the recited word takes the accent; by letter the letters to come wait at 18% and an accent underline glides along the line from word to word (`--qm-reading-pill`, 200ms), across a merge into the next word.
- **Read-along.** As the Mushaf app draws it: `reading.ts` builds the data SDK's `ReadingTrack` per chapter from the units (every take is a word visit; letter `k` of a take is `s:a:w:k+1`; silent or untimed letters are not recited letters) and the core's `readingAt` gives the playhead (`setReading`) by word or letter by letter (footer toggle, `reading.letters` follows it): the last recited letter stays lit between letters, silent letters follow their pronounced neighbour, a tanween or merged letter crossing into the next word lights there, a pause at a stop sign shows. The reciter's opening basmala (`0:2:w`) reads on the basmala line (`basmalaAt`).
- **Stops in the tajweed.** The rules of stopping (madd 'arid, leen, qalqalah) go where this recitation stops, not by the text alone: the SDK's `ReadingTrack.stops()` marks each word with a stop sign stopped when a visit of it is followed by a gap over `EARLY_MS` (`tajweed.stopsAt`); so of a mu'anaqah pair (2:2 رَيْبَ ۛ فِيهِ ۛ) only the one the reciter stopped on is coloured. Other surahs on the page follow their signs (the second of a mu'anaqah pair).
- **Read-along.** As the Mushaf app's: the data SDK's `ReadingTrack`, built per chapter from every take of every word and its letters (`reading.ts::chapterReading`; letter k of a take = `s:a:w:k+1`, silent/untimed letters left out), read by the core's `readingAt` each frame (`readingStateAt`; the opener basmala `0:2:w` reads on the basmala line via `basmalaAt`). Letters mode: the sounding letter lit, the last recited one stays lit between letters, silent letters follow their neighbour, a cross-word merge lights both words, a pause lights the stop sign. Word mode (`mushafLetters` off): the word alone.
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

**Full screen** (footer ⤢ button, `F` or `F11`; the window going full screen any other way — OS/browser full screen, detected via `(display-mode: fullscreen)` or window = screen size — enters the same mode without the Fullscreen API and leaving it exits; exiting the mode while the window stays full screen suppresses re-entry until it leaves, and `F11` out of an OS full screen is left to the browser; `Esc` / `F` / leaving the tab or view exits; `fullscreen.svelte.ts`, not persisted): `html.mushaf-full` hides the app header and the browser is asked for real full screen. If it refuses (embedded pane, iframe policy) the chrome still hides and the book scales to the window. After 1.2 s the footer **docks** (`html.mushaf-dock`: slides off the bottom, the book takes its height bar a 22 px strip). The whole 22 px bottom strip is the hover target (a chevron pill marks its centre) and brings it back overlaid on the book (`html.mushaf-dock-open`) on hover/focus/click; it slides away 1.2 s after the pointer leaves, unless something in it is in use (hovered, focused, a drop-up open). The view re-measures on toggle and on dock since the book's edges move without a resize.

`verse` + hide upcoming = memorisation mode (only what has been recited shows). `mushafActive = mushafMode && isHafs(playerContext.delivery.riwayah)`.

## Repeat (`repeat.svelte.ts`, `repeat-source.ts`, `MushafRepeatPanel`)

Mushaf-only, the Mushaf app's panel and player logic (`@qud-technologies/mushaf-core/app`): **Verses** = this verse / this page (the spread on show) / this surah / chosen verses (a surah and a verse for each end; a start after the end moves the end along), bounds by `repeatBounds`; **Each verse** 1, 2, 3, 5 or loop; **Whole range** 1, 2, 3 or loop (default loop); **Pause between** none, 1, 2 or 3 s. The plan's steps are `repeatSteps` over a `PlaySource` of the range's surahs the reciter has (`repeat-source.ts`); a step ends at `stepEnded`.

- Each verse is its **canonical take** (`canonical.ts`, a TS port of `qua_shared/timestamps_native.py::_canonical`: split on foreign-verse interleaving, first completing occasion, cut at completion, drop a leading false start), never the reciter's re-dos.
- No pause: a next step that starts where the audio already is plays straight on; any other seeks at once. A pause puts that silence between every two steps.
- Driven by `MushafView`'s rAF + the port's `timeupdate`. A seek the driver didn't make (footer skip, scrub, word click) ends the repeat. Cross-surah steps ask the view to switch chapter, then resume once that chapter is live. The footer badge shows the repetition of the verse playing.

## Sizing

The package's (`@qud-technologies/mushaf-core` `fit`, ported from QUA's `fit.ts` and checked bit for bit): natural word spacing, no kashida justification; a column sized to the 95th-percentile line; the base font = min(height fit at pitch 1.75, width fit for 1 or 2 pages); a dense spread shrinks its own font. The view gives the element its box: full width, height from the tab's top to the shell player's real top edge (not `--player-h`, which omits its progress rail; in docked full screen the window bar the 22px handle strip). The package's default gutters are QUA's (top 0, bottom 12, 8 inline, 44px nav slots, 2px spine).

## Data + assets

- **Data:** the Talqeen pack (`quran-core.qref`) through the TS SDK (`@qud-technologies/quran-data`, installed from its release tarball): page layouts of 1405/1421/1441, Digital Khatt v2 words, ayah-end markers, the basmala, and each layout's stop edition (1405 has its own; 1421 + 1441 the default). `lib/quran-data/pack.ts` opens it once from `/api/static/quran-data/quran-core.qref`.
- **Fonts:** the pack's `fonts()` (DK v2 for 1421/1441, DK v1 for 1405, surah-name v2, the QuranCommon surah frame), served by `/api/static/quran-data/<file>` (`fontUrl`). The package falls back to another text font when a layout's own one fails, measuring and drawing in the same face, and to plain text / a ruled box for the header fonts. App-wide, `styles/quran-fonts.css`'s `DigitalKhatt` face is the same DK v2 file from the same route, declared after `cells.css` (whose own face points at an unserved `/fonts/` path).

## Footer + shell in Mushaf view

- Hidden: the waveform, analysis row, owner validation panel, the shared NowReciting bar (`lib/stores/now-reciting-suppressed.ts`), shuffle (button hidden, auto-shuffle unarmed, `R` ignored).
- Also hidden (not greyed — they have nothing to act on): loop word, letters, phonemes, wipe, tajweed, the shortcuts help, report.
- Added: book toggle, Repeat drop-up, Mushaf settings drop-up, full screen (`mushaf/MushafFooter.svelte`, first in the analysis cluster).

## Files

`tabs/timestamps/mushaf/`: `MushafView` (the `<QudMushaf>` host: data, box, follow, clicks, keys, repeat host), `highlights.ts` (reading state → package highlights), `position.ts` (time → word anchor), `MushafFooter` / `MushafSettings` / `MushafRepeatPanel`, `fullscreen.svelte.ts`, `reading.ts`, `canonical.ts`, `repeat-source.ts`, `repeat.svelte.ts`. Tests in `__tests__/`. Look parity before/after a change: `scripts/diagnostics/mushaf_look_parity.mjs`.
