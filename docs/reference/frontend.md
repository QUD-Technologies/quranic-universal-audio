# Frontend

Three-tab SPA at `inspector/frontend/src/`. TypeScript (strict) + Vite + Svelte 5 runes. Built to `frontend/dist/`, served by Flask (`FRONTEND_DIST` in `app.py`).

## Stack

| Concern | Choice |
|---|---|
| Language | TypeScript strict (`strict`, `noUncheckedIndexedAccess`, `noImplicitAny`, `strictNullChecks`) |
| Bundler | Vite 5, `target: es2022` |
| UI | Svelte 5 (`@sveltejs/vite-plugin-svelte` ^4, mounted via `mount()` in `main.ts`) |
| Charts | Chart.js 4 + chartjs-plugin-annotation; split into a `charts` chunk via `rollupOptions.output.manualChunks` |
| CSS | Plain CSS in `src/styles/`, imported from `main.ts` (`tokens.css` first — defines the dark `:root` vars; `theme-light.css` last — the `[data-theme="light"]` override). Token-driven light/dark; canvas colours bridge via `lib/utils/canvas-theme.ts`. → [`theming.md`](theming.md) |
| Audio | Web Audio API; per-tab `AudioPort` transport (`lib/playback/`) |
| Unit tests | Vitest 3 + `@testing-library/svelte` 5 + `happy-dom` |
| E2E | Playwright (`npm run test:e2e`) |
| Typecheck/lint | `tsc --noEmit`, `svelte-check` (`npm run check`), eslint flat config |

`vite.config.ts` proxies `/api` and `/audio` to Flask `:5000` during dev (`:5173`). Two-stack mode via `INSPECTOR_VITE_PORT` / `INSPECTOR_BACKEND_PORT`. Sourcemaps dev-only.

## Svelte version convention

New code is **Svelte 5 runes**: `$state` / `$derived` / `$effect` / `$props` / `$bindable`, callback props. Legacy Svelte 4 syntax (`export let`, `$:`, `createEventDispatcher`, `on:` directives, `<slot>`) still compiles via the compat shim and is migrated opportunistically (plan: `docs/planning/svelte-migration.md`). A file is fully one mode or the other — never mixed.

**Currently still legacy** (notable): `App.svelte`, `AccordionGuideModal.svelte`, and the imperative canvas/audio components.

**Deliberately exempt (stay legacy indefinitely):** `TimestampsWaveform.svelte` and the canvas/audio-imperative components (`WaveformCanvas.svelte`, `SegmentWaveformCanvas.svelte`, and the per-frame overlay drivers).

## Layered diagram

```
main.ts (mount App, import global styles, installAudioWarmup)
  └── App.svelte  (header/tab-bar, auth controls, lazy tab mount, global popover/modal/toast/bookmarks)
        ├── tabs/dashboard/DashboardTab.svelte   → views/{CatalogList,ReciterDetail} + BottomPlayer
        ├── tabs/timestamps/TimestampsTab.svelte → {components,stores,utils,services}/
        └── tabs/segments/SegmentsTab.svelte     → {components,stores,utils,domain,types,guides}/
```

Tabs are lazy-mounted (`{#if mountedTabs.has(tab)}`); once visited they stay in the DOM `hidden`, preserving state across switches. `AudioPort`s are paused on tab-leave by `applyTabSideEffects` — `segPort` (Segments) and the shared `dashPort` (Dashboard **and** Timestamps, via `BottomPlayer`); `tsPort` is defined but vestigial. `lib/` is strictly cross-tab; `lib/` never imports `tabs/`, and no tab imports another tab's dir. Audio playback internals (AudioPort, kill-switch, peaks) live in the **`inspector-audio` skill**, not here.

The **Dashboard tab** is the entry view: public reciter browse/search/filter/play for all visitors, plus admin (maintainer/owner) controls — activity rail, claim/state actions, request review. It is the default landing tab (legacy `insp_active_tab='audio'` redirects here; the old Audio tab is removed — `App.svelte::cleanupLegacyAudioKeys` sweeps `insp_aud_*` localStorage keys).

## `lib/` — cross-tab only

### `lib/actions/`

| File | Role |
|---|---|
| `editGate.ts` | Svelte action gating any edit-trigger click on `editingMode`; passes through for `editor`/`maintainer`/`owner` (or admin-only with `{require:'admin'}`), else swallows click + surfaces `EditAffordancePopover` or sign-in modal. Capture-phase. |
| `click-outside.ts` | Action: callback on pointerdown outside the node (popover/dropup dismissal). |

### `lib/api/`

| File | Role |
|---|---|
| `index.ts` | Single fetch boundary — `fetchJson` / `fetchJsonOrNull` / `fetchArrayBuffer` typed helpers |
| `auth-client.ts` | `signIn` (→ `/api/auth/login`) / `signOut` (→ `/api/auth/logout` + reset `currentUser`) |
| `claims-client.ts` | Claim POSTs; maps 200/401/403/409 → row resolve / sign-in modal / toast |
| `dev-role.ts` | `POST /api/dev/role` (dev-mode only; 404s on Space) |
| `reciter-task.ts` | `readable` store polling `/api/reciter-task/<slug>` @30 s while subscribed |
| `public-reciters.ts` | Read-only `/api/public/*` reciter list fetchers (no auth, `max-age=30`) |
| `public-reciter-detail.ts` | Single-reciter detail; `null` on 404 |
| `public-activity.ts` | Public activity feed (`/api/public/activity`, six-event allowlist) |
| `public-activity-admin.ts` | Owner-only `DELETE /api/public/activity/<audit_id>` — global tombstone for a public-feed card. (The admin notifications rail was retired; admin awareness lives in the Admin dashboard tabs now.) |
| `requests.ts` | Request submit (contributor+) / admin review-reject / undiscard (owner) |

### `lib/playback/`

| File | Role |
|---|---|
| `audio-port.ts` | `AudioPort` — single transport chokepoint; owns `<audio>`, CBR-chapter-vs-VBR-clip src swap, file-absolute ms via `offsetMs` |
| `audio-graph.ts` | Web Audio kill-switch — sample-accurate gain ramp (`cutAudio`/`uncutAudio`) to flush OS-queued tail |
| `audio-range.ts` | `AudioRange` — one-rAF-loop `[startMs,endMs]` playback with pluggable boundary policy (stop/loop/advance) |
| `dash-port.ts` | Dashboard-tab `AudioPort` instance (`dashPort`) |
| `play-url.ts` | **Play-URL resolver** — the one place deciding direct-CDN vs `/api/seg/audio-proxy` src for a chapter MP3: one cached head probe per chapter URL (`probeDirectPlayable`: host must answer a CORS Range request with 206 AND the file's first frame must not carry a `Xing` VBR tag — `mp3-header.ts`; Chrome seeks `Xing`-tagged files via the 1/256-quantised TOC and lands seconds off, the bucket copy is remuxed with `Info` and seeks exactly), sync `playUrl()` at `setSource` time (unknown URL ⇒ proxy, never silence, never drift). Every src builder (`segments/…/source.ts::wrapCbrSrcIfBySurah`, `dash-prewarm.ts::dashProxyUrl`, warmup, TS shuffle) goes through it. Motivation: far-from-US clients get ~300–500 KB/s from the Space, and a whole-chapter stream through it starves every other request on the HTTP/2 connection. Segments tab: chapter load probes only the selected chapter, so `playback.ts::playFromSegment` parks a CBR play whose URL still `needsDirectProbe()` (a cross-chapter accordion card) until the probe settles instead of starting it on the proxy; `warmup.ts::warmSegChapter` (accordion lead card, play-button hover) probes first and does the hidden-`<audio>` whole-chapter `shadowPrewarm` only for a direct chapter — a proxied one gets just the 64 KB `warmSeg` Range. The accordion card-0 warm fires once per category open, not per validation refresh (autosave drops the fixed card, which used to re-fire it mid-play) |
| `shadow-audio.ts` | Hidden `<audio>` for cross-chapter prewarm (validation accordion) without disturbing `segPort` |
| `constants.ts` | Playback constants (coverage padding, etc.) shared across port/range consumers |

### `lib/components/`

| Path | Role |
|---|---|
| `WaveformCanvas.svelte` | Base canvas peaks renderer; exposes canvas for imperative overlays (legacy/exempt) |
| `AudioElement.svelte` | Thin `<audio>` wrapper (safePlay, events) |
| `AudioPlayer.svelte` | Full audio player UI |
| `SearchableSelect.svelte` | Dropdown with fuzzy search + grouped options |
| `SpeedControl.svelte` | Playback speed selector |
| `Modal.svelte` | Generic modal shell — focus trap, Esc/backdrop close, body scroll lock. Its backdrop portals to `document.body` (`lib/actions/portal.ts`) so a deeply-mounted modal escapes ancestor stacking contexts (e.g. the sticky dashboard rail) and paints above fixed app chrome like `BottomPlayer`. |
| `SignInModal.svelte` / `ToastHost.svelte` | Root-mounted sign-in modal + toast host |
| `EditAffordancePopover.svelte` | Single global popover surfaced by `editGate` |
| `DevRoleSwitcher.svelte` | Dev-mode role switcher (gated on `$currentUser.dev_mode`) |
| `BookmarksPanel.svelte` | Browser-local bookmarks sidebar |
| `ClaimButton.svelte`, `StatePill.svelte`, `CoveragePill.svelte`, `FilterPill.svelte`, `ReciterChip.svelte`, `ReciterRow.svelte`, `SearchInput.svelte`, `ExternalLinks.svelte` | Catalog/dashboard chips, pills, rows |
| `picker/` | `CombinationPicker` + `PickerFilterRail` / `PickerFooter` / `PickerStateTabs` / `PickerReviewStatus`. For callers holding `reviews.view`, the picker lazily fetches `/api/admin/reviews/list` on open and decorates `under_review` rows with the claimer + a marked-ready badge (`PickerReviewStatus`) — same data as the Admin → Reviews tab. Non-capable callers fire no admin fetch. |
| `player/` | `BottomPlayer`, `PlayerControls`, `PlayerProgress`, `PlayerMetaChip`, `SpeedPopover`, `SurahPopover` |

Shared player rule: deliberate navigation resumes playback even when the audio was paused. This includes ayah/surah steps, surah or delivery changes, progress/filmstrip/line seeks, Timestamps keyboard navigation, validation jumps, and manual shuffle/random-reciter triggers. Passive inspection (popovers, hover previews, zoom/pan, speed/download/bookmark/display toggles) must stay paused.

Recitation-animation (`lib/recitation-animation/`, mounted by `NowReciting` above the player in both Dashboard + Timestamps tabs): the `AyahFilmstrip` ayah scrubber and the `LineAnimation` teleprompter are **recitation-driven, not clock-driven**. Both map playback time → the recited word via the shared `findActiveAt` (`recitation-active.ts`): a flat per-occurrence interval timeline that returns `null` during silence gaps and travels backward on loopbacks (the raw, non-deduped occurrences the shard retains "for the filmstrip" — see [timestamps-job.md](timestamps-job.md) §1a). The filmstrip's cursor crosses each verse cell by **recited time over the verse's CURRENT take** (`takeFrac` in `AyahFilmstrip.svelte`): the cell is traversed at one constant velocity (`aw ÷ that take's recited seconds`), so a re-recited verse conforms to its OWN duration and the speed never lurches word-by-word within a cell. The take is the maximal run of consecutive timeline intervals that stay in the cell and advance in reading order (a loopback or another verse breaks it; a within-verse pause does not — its silence isn't summed); for a forward first take this is **identical** to the first-occurrence word-fraction crossing. A within-verse word loopback rewinds the cursor and a cross-verse loopback scrolls the strip back (same glide in both `snap`/`hybrid` modes). The bottom `PlayerProgress` scrubber stays time-linear — only the cell bar is recitation-adaptive.

**Strip scroll motion** is unified in one controller — `createStripScroller` (`filmstrip-scroll.svelte.ts`) — shared by every motion model (`snap`/`hybrid`/`tuner`) and every play/pause path, with one curve and one set of constants, owning its own rAF so a glide animates whether or not audio is playing. Continuous progression — forward playback, live drag/scrub, and the between-verse gap-scroll below — tracks the live position **instantly** (`snap`/`follow`), so it moves at the ruler velocity (`filmstripPxPerSec`). Every *discontinuity* (within-verse rewind, multi-verse loopback, click, key, snap-center, drag-release, paused refresh) instead runs one distance-proportional **Hermite** glide (`glide`, or the `follow` catch-up), **independent of the ruler velocity** — a jump is smoothed, not replayed in time. A catch-up chasing a moving target re-plans each frame **carrying current velocity**, so it never lurches and never inherits a stale cutoff. A **natural loopback** (the reciter re-reciting — a backward recitation jump with no audio-time seek) runs **no separate motion path**: each verse cell is simply crossed over its OWN take's recited seconds (the `takeFrac` rule above), so a multi-verse re-take replays every verse at that verse's own second-take pace — **each conforming to its own duration**, never collapsed onto the loopback verse's. The backward landing is one Hermite glide (the `isJump` discontinuity), and the loopback's pause-back is scrolled by `scrollThroughGap` (below), so by the replay's first word the strip is already at the landing. `AyahFilmstrip` keeps only the recitation-discontinuity *detection* (`isJump` / `SEEK_JUMP_MS`) and the one-shot fill-bar glide (which shares the controller's `glideDur`).

**Silence handling.** A "pause" is just a positive timing gap between consecutive units (contiguous units share an ms-quantized boundary, so a gap means the aligner inserted `sil`/`sp` — a detected silence). Each surface surfaces it:
- **Filmstrip** — `snap` mode freezes during silence (active cell de-highlights, no needle). The **scrolling** modes (`hybrid`/`tuner`) no longer freeze: the needle stays visible but **greys** (`--text-muted`) and scrolls **continuously** across the inter-cell gap for a between-verse pause **in either direction** — forward, and the backward gap of a loopback re-take (`scrollThroughGap` interpolates offset by elapsed-silence, anchored to the real interval the silence follows; `nextIntervalAfter` resolves the upcoming cell, the guard is `nextIdx !== frozenIdx`). Within-verse / leading / trailing silence holds. The strip is a **fixed-scale time-ruler at one global velocity** (`config.filmstripPxPerSec`, default 12 px/s): cell width = `max(filmstripMinCellPx, canonDurSec × pxPerSec)` and inter-cell gap = `VerseCell.nextGapSec × pxPerSec` (between-verse silence, measured from the verse's real `canonEndSec` so within-verse pauses never leak in). The cursor therefore travels at this one velocity everywhere — within cells and across silences — and a given silence renders to the **same px in every surah and for every reciter** (no per-chapter normalization; the prior `maxCellPx / maxDur` anchor scaled silences to the longest verse, so they flattened on long surahs). Geometry is identical across motion modes (toggling never shifts the strip — only the cursor's motion differs). A too-short verse is **floored to `filmstripMinCellPx`** (18) so its ayah number stays legible; to keep the floor from speeding the cursor up, each cell carries a time-true span `aw = canonDurSec × pxPerSec` and the cursor crosses only that **centered** span at the ruler velocity (`offsetForReci`), the surplus `w − aw` **absorbed into the adjacent gap** (where the greyed needle already scrolls the silence). The per-cell progress fill bar reads the **cursor's** position within its cell (`(offset − cumBefore)/w` on the cell the cursor sits in, `fillIdx`), so the bar's leading edge always sits under the needle — on floored cells and through a loopback re-take alike. An unrecited placeholder, which has no recited time, gets a fixed visible width. A near-zero silence floors to `filmstripGapPx` (2). No max cap — a long verse is just a wide cell scrolled through at constant velocity.
- **Analysis view** (`TimedAnalysisRow`) — wraps `@quranic-phonemizer/cells/AnalysisRow`. The package renders native words, columns, groups, bridges, sounds, and boundary cells; the wrapper derives column highlighting from native owned/presented sound IDs, extracts only recorded timing gaps as pauses, and adds seek/loop, waveform-hover, report, and context behavior through public `data-qc-*` hooks.

**Native model.** `native-shards.ts` validates shard v13 and parses each reading with the shared package. The Inspector has no cell/group/phoneme projection model. `TimedAnalysisRow` keeps one native-ID-to-DOM cache and one RAF loop; `timed-entities.ts` resolves native timing spans for interactions.

**Column alignment.** The package uses native column IDs, group membership, sound ownership, and bridge documents directly. `timestamps.css` contains only Inspector timing/highlight/report/context overrides and host theme tokens; it does not define letter, diacritic, phoneme-grid, bridge, waqf, or underline geometry.

**Row layout.** `AnalysisRow` renders compact native merger runs and keeps bridge junctions unbroken. `verseFlow='inline'` gives cross-verse readings separate inline verse blocks with a verse marker; focus changes apply context opacity without rebuilding geometry. The wrapper retains the existing capped row-gap measurement for Inspector-level wrapping only.

**Cell interactions.** The wrapper resolves a clicked or hovered `data-qc-*` identity to its native interval. Single click seeks (or moves an active loop); double click toggles a loop; hover publishes the waveform band. Duration/rule/report tooltips are Inspector addons. A boundary without a callback remains visibly static and non-clickable; Inspector sakt is always such a pausal boundary.

**Tajweed underlines + settings.** `utils/tajweed-rules.ts` is a declarative policy over the 45 producer rule IDs. It owns Inspector legend grouping, duration, color, enable toggle, stack layer, and locale label selection; an unknown producer ID fails tests. The package owns actual rule placement and underline/cell geometry. Qalqala akbar retains its native hover name but shares the qalqala toggle/color and kubra wrap. Hover-only rules remain named in tooltips without a legend row or underline. Defaults are off only for iẓhar, iẓhar shafawi, and madd ṭabīʿī.
- **Teleprompter display settings** — the persistent left-hand controls are silent-letter policy · word/letter granularity · upcoming-text visibility. The silent-letter control is shown only in letter mode, where it has an effect; its persisted choice is retained while word mode is selected. Word and letter granularity each persist their own upcoming-text opacity, and switching granularity restores that mode's last choice. Shaped letter mode keeps one complete base SVG silhouette at the upcoming opacity in every word state, then paints only reached/current tokens as opaque overlays. In Full visibility the base is already opaque, so completed non-active token overlays switch off and each previous word is painted exactly once; the active token overlay remains available for highlighting. Intentional Arabic joining overlaps therefore composite once even inside the active word, while each producer animation token remains independently highlightable. Repeated word takes project timing and sound ownership from their own occurrence by within-word token order and exact token text; v13 token IDs are positional inside one native reading and are not identities across a stop/wasl repeat. Foundation tokens never pass back through the legacy grapheme stamper, which would fold combining marks such as an independently sounded small wāw into their host. Silent-omit derives highlight ownership directly from the selected occurrence's `animationTokens[].sound_ids`: a token with an empty list reveals at its borrowed time but stays in the base colour, while genuine co-sounding merger contributors retain the shared sound ID and highlight together. A solar article lām deliberately publishes an empty list even though its source unit geometrically presents the following solar sound; its cohighlight target remains intact so normal mode preserves the legacy paired highlight. A sounded dagger alef is a separate timed paint target from its silent carrier, so forms such as ṣalāh, ribā, and alif-maqṣūra-plus-dagger colour both in normal mode but only the dagger in silent-omit mode. DigitalKhatt's display-only noon+iqlab mini-mīm ligature adds a geometry-only `silent_companion` role: the sounding `meemiqlab` path keeps the token highlight while the silent noon body and dot remain visible only through the base silhouette in silent-omit mode. Standalone mark tokens retain their exact fill highlight but omit SVG glow so the glow cannot spill onto an adjacent seat/host (for example `لَهُۥ` in 21:88).
- **Teleprompter** (`LineAnimation`) — stop marks are split out of the reveal (`build-structure.ts` → `AnimWord.clean`/`waqf`) so they never join the per-letter/word highlight, and rendered as a separate `.ra-waqf-mark` **overlay**. A combining mark is painted in the colour of whatever run it shapes into (true for HTML *and* SVG `<tspan fill>`), so to get the mark's natural font-anchored position *and* an independent colour/opacity, the overlay is a second copy of the full word in one run, `clip-path: inset(0 0 70% 0)`-clipped to the high waqf register so **only the mark paints** (the letters are clipped away → no extra ink, opacity is fully independent, no bleed into the per-letter reveal). The reveal sweep (`sweepWaqf`) adds `.revealed` (opacity→1) when the mark's own last letter is reached, and `.waqf-active` (highlight) only while a pause holds on its word — it never gets the reveal highlight.

Stop-sign detection is one shared util — `lib/utils/waqf.ts` (`splitWaqf`/`hasWaqf`), the surfaced marks being U+06D6–U+06DB **excluding** U+06D9 (لا, "do not stop"); U+06DC (saktah) is also not surfaced.

**Coverage markers.** `load-chapter.ts` derives a per-chapter `coverage` (incomplete + fully-missing verses) entirely client-side — diffing the recited words against the reference word counts from the qpc verse index (`ts-source.loadQpcVerseIndex`, one memoized scan of the qpc the animation already loads; `recitation-data/coverage.ts`, mirroring the backend `select_complete_verses` gate). `buildFilmstripModel(units, weighting, coverage?)` then tags present cells `none`/`words` and inserts a `full` **placeholder cell** for each unrecited verse (no units/geometry) so the gap stays visible. In `AyahFilmstrip`, a `words` cell rings red (`--state-missing-*`, still clickable + plays), a `full` cell is a dimmed red placeholder that playback + all user-nav (click/arrow/drag, both motion modes) skip (non-clickable). The active cell's status is reported via `onActiveCell`, and `NowReciting` shows a contextual "missing words" pill in its handle row **only** while the selected verse is itself incomplete.

**Cross-verse waṣl.** A reciter continuing from verse N into N+1 is one native v13 reading whose ordered `parts` preserve the original verse occasions. `occasions.ts` exposes `ChapterOccasion.waslOutTo`; `recitation-data/wasl.ts` derives the connected playback span. Naming guard: "bridge" is reserved for the phonemizer's native cross-word merger — everything cross-verse is `wasl*`. Surfaces:
- **Filmstrip** (`AyahFilmstrip`) — a bridged run renders as a gapless **mega-cell**: full-bordered sub-cells keeping their own numbers, tied by one accent rail under the row (`waslGroups`); `VerseCell.waslNext` (set in `filmstrip-model._assemble` from `TimeSpan.waslTo`) pins the gap to 0 and dissolves the min-width floor so the group crosses at one velocity.
- **Teleprompter** (`LineAnimation`) — `wasl-chains.ts` precomputes the chains from `units`; the page extends to the whole chain (`ayahEndIdx`) and clears only on a chain change, so N+1's words flow onto the same line and the `۝` marker sits inline. The marker silence-colours (`.marker-pause`) on a real waqf stop at a non-bridging verse end.
- **Analysis + waveform** (Timestamps) — `TimestampsTab` selects the native reading containing the active occasion and passes it to `TimedAnalysisRow`. Cross-verse parts stay one wasl reading and render as inline verse blocks; non-focus parts dim and become non-editable without changing phonemes. `TimestampsWaveform` spans the connected audio window and uses the same native timing index for highlight, seek, loop, and hover.
  - **Native junction** — the reading's existing native merger/bridge crosses the verse boundary unchanged. The frontend never synthesizes it.
  - **Recorded-pause packing** — the timing sidecar identifies recorded pauses and passes them through the renderer's `keepBoundaryWithNext` host predicate. The renderer keeps `word + pause + word` as one unbreakable run, so the Inspector's dynamic row spacing stays outside the junction and the pause marker remains symmetric between its words.

### `lib/catalog/`, `lib/refs/`, `lib/icons/`

| Path | Role |
|---|---|
| `catalog/schema-descriptor.ts` | Schema descriptor for the dashboard secondary facet rail (operates on combinations/`PublicDelivery`) |
| `refs/quran-refs.ts` | `dk_words` + `verse_word_counts` behind one content-hashed endpoint; fetched once per browser, shared via store |
| `icons/Icon.svelte` + `*.svg` | Inline SVG icons via `{@html}` (`currentColor` cascade) |

### `lib/types/`

| File | Role |
|---|---|
| `generated/schemas.ts` | **Codegen'd FE data contracts** — `scripts/codegen/regen_fe_types.py` over `qua_shared/schemas/fe_types.py` (Pydantic → JSON Schema → TS). Never hand-edit; CI `schema-codegen-check` gates it. The `bucket/` artefact shapes (`Segment`, `EditOp`, `PeakBucket`, `HistoryBatch`, `PhonemeInterval`, …) **and** the `wire/` `seg`/`ts`/`public`/`audio` request/response shapes are now modeled there and codegen'd. |
| `view-models.ts` | **FE-only** view-models + derived reads with no single wire producer: the editor's working `Segment` superset, the `EditOp`/`EditOpPatch`/`HistoryBatch` history views, `GenerationBoundary`/`HistorySummary` rollups, `Actor`, the `Ref`/`VerseRef` string aliases, and the derived `/api/seg/*` reads (`SegEditHistoryResponse`, `SegStatsResponse`, …). |
| `peaks-transport.ts` | **FE-only** waveform transport — `PeakBucket`/`AudioPeaks`/`SegmentPeaks`, including the flag-gated `Int8Array` drawer branch (no wire model). |
| `ts-client.ts` | **FE-only** Timestamps-tab client types — the `TsVerseData` verse model, slim `TsCatalog*` projection, native-v13 response aliases, and `SurahInfo*`. Native shard document types come from generated shared schemas. |
| `public-bucket.ts` | **FE-only** public-bucket display vocabulary — `PublicBucket`/`AdminBucket`, the `PUBLIC_BUCKET_LABELS`/`PUBLIC_BUCKETS`/`BUCKET_PRIORITY` tables, `bucketRank`, and `CoverageKind`. |
| `ui.ts` | Shared UI types for components |

### `lib/stores/` (cross-tab)

| File | Role |
|---|---|
| `current-user.ts` | `currentUser` writable (loaded from `/api/me`); `isSignedIn()`; derived `isAdmin` (maintainer\|owner), `isOwner`; `loadCurrentUser()` / `resetCurrentUser()`. Dashboard-level gating. |
| `editing-mode.ts` | `editingMode` writable (`view`/`editor`/`maintainer`/`owner` + `viewReason`); pure `syncEditingMode(user,task)`; derived `editingDisabled`, `isAdmin`. Reciter-scoped gating — see `editGate`. |
| `edit-popover.ts` | State for the `editGate`-surfaced popover (`showEditPopover`) |
| `sign-in-modal.ts` | `openSignInModal` state |
| `toast.ts` | Toast queue |
| `bookmarks.ts` | Bookmarks panel toggle |
| `player-context.ts` | Cross-tab bottom-player context |

### `lib/utils/`

| File | Role |
|---|---|
| `active-tab.ts` | Active-tab store + get/set |
| `constants.ts` | `LS_KEYS`, `TAB_NAMES` |
| `animation.ts` | `createAnimationLoop()` — rAF loop with start/stop |
| `chart.ts` | Chart.js bootstrap (registers plugins) |
| `audio.ts` | `safePlay()` (swallows AbortError), `audioSrcMatches` |
| `audio-warmup.ts` | First-gesture audio decoder/output warm-up |
| `preconnect.ts` | DNS preconnect helper |
| `peaks-fetch.ts` | Cross-tab `/api/seg/segment-peaks` fetch |
| `peaks-view.ts` | Peaks-shape adapter (legacy nested `[min,max][]` vs int8) |
| `waveform-cache.ts` | Normalized-URL → peaks Map (non-reactive) |
| `waveform-draw.ts` | Peaks → canvas draw (`drawWaveformPeaks`) |
| `arabic-text.ts` | `stripTashkeel`, combining-mark helpers |
| `waqf.ts` | Quranic waqf (stop) marks — `splitWaqf`/`hasWaqf` (U+06D6–U+06DB, excludes لا U+06D9 + saktah U+06DC). Shared by the analysis pause bridge + teleprompter stop-sign handling |
| `fuzzy-match.ts` | Arabic-normalizing search: memoized `normalizeArabic`, `match`, and `filterByFields` (the shared multi-field filter behind every reciter/option search bar) |
| `list-virtualization.ts` | Pure prefix-sum windowing helpers for variable-height virtual lists (CatalogTable + SegmentsList) |
| `surah-info.ts` | `surahInfo` data + ready promise + `surahOptionText` |
| `grouped-reciters.ts` | Grouped reciter dropdown options |
| `keyboard-guard.ts` | `shouldHandleKey(e, tab)` |
| `derived-eq.ts` | Deep-equality derived store helper |
| `svg-arrow-geometry.ts` | `computeArrowLayout()` for history diff arrows |
| `word-boundary.ts` | Word-boundary helpers |
| `speed-control.ts` | Speed option list |
| `formatting.ts` | `_formatBytes` |
| `facets.ts` | Faceted-count helper (history filters, dashboard rail, picker) |
| `visible-poll.ts` | Page-Visibility-aware polling |
| `relative-time.ts` | "3 hours ago" labels for activity/detail |
| `axis-labels.ts` | Resolve schema axis/tag keys → human labels |
| `delivery-label.ts` | Catalog vocab slug → display name |
| `delivery-sort.ts` | Canonical combination sort order used by reciter detail, player switcher, and dashboard row play defaults |
| `countries.ts` | ISO-3166-1 alpha-2 list (build-time embedded) |

## Tabs

### `tabs/dashboard/`

Entry view: public catalog browse/search/filter/play + admin controls.

| Path | Role |
|---|---|
| `DashboardTab.svelte` | Root — `CatalogList` + `ReciterDetail` modal + `BottomPlayer` |
| `views/CatalogList.svelte` | List view; filters on combinations, grouped back by reciter |
| `views/ReciterDetail.svelte` | Reciter detail modal (flat combination table, status-priority sort) |
| `components/CatalogTable.svelte` | Virtualized reciter list (own scroll container; windowing via `lib/utils/list-virtualization.ts`) |
| `components/DetailHeader.svelte`, `FactsList.svelte`, `StateTimeline.svelte` | Detail/table pieces |
| `components/ActivityRail.svelte` | Public activity feed (owner-visible delete affordance on each card) |
| `components/RequestForm.svelte`, `submit/{SubmitWizard,StepReciter,StepSource,StepDetails}.svelte` | Request/submit flow |
| `stores/catalog-data.ts` | Full public reciter roster (paginated) + stats, cached in-memory; visibility-aware poll gates the multi-page refetch on the `/api/public/version` (`db_seq`) probe — an idle catalog costs one tiny request per tick, not a full roster page-through |
| `stores/dashboard-state.ts` | Filter/sort/search + detail-modal flag (mounted at root, survives modal) |
| `stores/submit-wizard.ts` | Submit-recitation wizard FE state (3-step) |

### `tabs/timestamps/`

Waveform + native analysis for published reciters (plus owner preview of generated-but-unreleased shards). Shards are compact native schema-v13 `.json.br` documents served with HTTP Brotli content encoding; each maximal connected wasl chain is one reading and its `parts` retain every recorded occasion. `ts-source.ts` decodes the renderer-neutral compact payload and chooses the active part/reading from the playhead without deduplicating retakes or reconstructing cells.

| Path | Role |
|---|---|
| `TimestampsTab.svelte` | Shell — reciter/chapter/verse cascade, config→CSS vars, view toggle, keyboard, waveform animation (imperative) |
| `components/` | `TimedAnalysisRow`, `AnimationDisplay`, `TimestampsWaveform` (exempt), `TimestampsControls`, `TimestampsAudio`, `TimestampsKeyboard`, `TimestampsViewControls`, `TimestampsShortcutsGuide`, `TsValidationPanel` (owner low-confidence accordion) |
| `components/` (footer) | `TimestampsFooterLeft` (reciter picker + shuffle), `TimestampsFooterReport` (report drop-up), `TimestampsFooterAnalysis` (loop / letters / phonemes / tajweed / help), `TimestampsFooterOpenSegments` (→ Segments editor redirect) — filled into `BottomPlayer`'s `meta` / `loc-lead` / `center-trail` / `download-lead` slots by `App.svelte`, only while the Timestamps tab is active |
| `services/ts_client.ts` | Shard-fetch data layer |
| `utils/` | `constants.ts`, `loop-target.ts`, `zoom.ts`, `range-spec.ts`, `audio-load.ts` |

**Public reports.** `TimestampsFooterReport` opens the category menu. Timing, Tajweed, phoneme, and silence flows enter in-grid native-target mode and submit batches to `/api/ts/<slug>/reports/batch`; audio and other use the inline verse composer. Report-only untimed entities remain selectable but never participate in playback highlighting. Owners get a `ts_report.created` notification on a new report and the reporter a `ts_report.resolved` on resolution; both rail redirects use `gotoTimestamps(slug, verseKey)` (`lib/utils/goto-timestamps.ts`) → the shared `pendingTsNavigation` channel → `jumpToTarget`.

**"Open in Segments" redirect.** `TimestampsFooterOpenSegments` (the ↗ next to the footer download button) deep-links the focused reciter + verse into the Segments editor via `gotoSegments(slug, { focusVerse: { slug, chapter, verse } })` (`lib/utils/goto-segments.ts`). `SegmentsTab` consumes the `focusVerse` intent once the target reciter's corpus is resident (`segAllData !== null`, the `clearPerReciterState` null being the fresh-corpus barrier) — switching to the verse's chapter, loading it, then scrolling the verse's first segment row into view via `targetSegmentIndex` (the same path Go-To / verse-jump use). The `slug` guard + a post-`tick()` re-validation reject a stale corpus mid-switch.

**Stores:** `verse.ts` (reciter/chapter/verse selection + `loadedVerse` + `validationData`), `display.ts` (view mode, granularity, show-tashkeel/phonemes; localStorage-persisted), `playback.ts` (auto-play, `currentTime`; defines `tsPort` but the tab plays through the shared `dashPort`), `zoom.ts` (slice-relative visible window).

### `tabs/segments/`

Full WIP editor — trim/split/merge/re-reference, auto-validation on save.

| Path | Role |
|---|---|
| `SegmentsTab.svelte` + `ShortcutsGuide.svelte` | Shell — dropdowns, filter bar, nav banner, list, CSS-var config, keyboard |
| `components/list/` | `SegmentsList`, `SegmentWaveformCanvas` (exempt), `Navigation`, `TimeEdit`, `TimeRange` (windowing math in shared `lib/utils/list-virtualization.ts`) |
| `components/filters/` | `FiltersBar`, `FilterCondition` |
| `components/edit/` | `EditOverlay`, `MergePanel`, `DeletePanel` |
| `components/history/` | `HistoryBatch`, `HistoryOp`, `HistoryArrows`, `EditChainRow` |
| `components/save/` | `SavePreview` |
| `components/stats/` | `StatsPanel`, `StatsChart`, `ChartFullscreen` |
| `components/validation/` | `ErrorCard`, `MissingWordsCard`, `MissingVersesCard`, `AccordionGuideModal` (legacy) |
| `components/footer/` | tab footer |
| `domain/` | `identity.ts`, `inverse-patch.ts` (paired with backend `domain/`), `registry.ts` (issue registry — paired with backend), `sorting.ts` (accordion sort abstraction) |
| `types/` | `segments.ts`, `stats.ts` |
| `guides/` | Accordion help-modal guide system — see `accordion-guides.md` |

**Stores:** `segments.ts` (normalized state by uid, IS-7), `edit.ts` (bundled 8-field edit state + `activeTrimBoundary` + `splitPreviewSelection`), `history.ts` (edit-history view), `validation.ts` (`SegValidateResponse`), `validation-sort.ts` (per-accordion sort prefs, localStorage `insp_seg_val_sorts`), `save.ts` (save-preview), `autosave.ts` (localStorage pref), `active-actions.ts` (focused-row action bundle for keyboard dispatch), `chapter.ts`, `chapter-meta.ts`, `config.ts`, `dirty.ts`, `filters.ts`, `navigation.ts`, `playback.ts` (`segPort`), `stats.ts`, `accordion-pin.ts`, `merge-redirect.ts`, `undo-pending.ts`.

**`utils/` (grouped):** `accordion-nav.ts` (DOM-read card sequence for ↑/↓ + autoplay inside an open accordion), `data/` (selection/filter/reciter/chapter actions, config loader, references, per-reciter state clear), `edit/` (trim/split/merge/delete/ignore/reference/setIsWasl/auto-fix/enter/common), `history/` (loader/actions/chains/items/render), `playback/` (`playback.ts` playhead overlay, `play-range.ts`, `range-spec.ts`, `preview.ts`, `resolvers.ts`, `source.ts`, `warmup.ts`, `row-registry.ts`, `chime.ts`), `save/` (payload/preview/execute/undo/actions), `validation/` (classified-issues, conf-class, card-lead-seg, missing-verse-context, refresh, resolve-issue, split-group, stale), `waveform/` (`draw-seg.ts`, `split-draw.ts`, `trim-draw.ts`, `op-peaks.ts`, `peaks-cache.ts`, `utils.ts`).

**Sample word review.** Samples open and stay inside the Samples sub-tab; returning to Editor restores the previous normal recitation. Sample slugs are not persisted as the Editor's reciter selection. Sample rows may carry historical `word_timings` from `detailed.json`. Both segment read routes expose the intervals, and `SegmentRow` highlights the sounding word during playback only in sample mode. The highlighted words come from the same edition-specific Quran refs and verse-marker rendering as ordinary rows, never the historical timing payload's text. `utils/samples/word-timing.ts` owns interval lookup, rejects timings whose first/last locations no longer match the row reference, and disables highlights if the timed coordinates cannot reproduce the displayed row text; save handling drops timings after a trim or reference change and preserves them across unrelated chapter rebuilds.

**Segment-end chime** (footer whistle toggle, `segmentEndChimeEnabled` + `utils/playback/chime.ts`): a short two-tone beep at the join between one segment and the next while autoplay walks the list, so the boundary is audible instead of only visible in the highlight. Default OFF, persisted to `LS_KEYS.SEG_END_CHIME`. Meaningful only under autoplay (autoplay OFF stops at each segment end — there is no next segment to announce), so the footer button is **disabled** rather than hidden while `autoPlayEnabled` is false, and `_chimeSegmentEnd()` re-checks `autoPlayEnabled` at every fire site so a persisted `true` can't leak a beep into non-autoplay playback. Three fire sites in `playback.ts`, one per way autoplay reaches the next segment: the accordion advance in `_onRangeBoundary`, the forward jump in `_maybeSkipDeletedGap`, and the chapter-continuous crossing in `onSegTimeUpdate` — the last gated by `_isNaturalAdvance` (playing, and the playhead landed within `NATURAL_ADVANCE_TOLERANCE_MS` of the segment it just left) so dragging the seek bar across many segments doesn't machine-gun the chime. All three route through `_chimeSegmentEnd(resume)`, which **opens a silent gap** (`CHIME_GAP_MS` = the chime's own length plus air) so the beep is audible instead of overlapping the next segment's opening: `pauseAndFlush` the port (draining the OS sink too), chime, then run `resume` — which is the advance itself, so the next segment starts only after the beep. With the chime off `resume` runs on a microtask and playback is byte-for-byte what it was before. Two consequences are handled explicitly: `stopSegAnimation` ignores the gap's `pause` (the port pauses only to make the silence, so surfacing it would flicker the footer play glyph at every boundary — the same guard `editMode` already had for the loop seek-back), and `cancelChimeGap()` drops a pending resume whenever something else takes the transport (`onSegPlayClick`, `playFromSegment`, `onSegAudioEnded`, `disposeSegPlayback`) so a stale gap can't restart audio the user just stopped. The tone is synthesized on the shared `AudioContext` (`_getCtx`) rather than shipped as an asset — no fetch, and no LFS-pointer trap for a binary under `public/`; it no-ops when Web Audio is unavailable, and a 250 ms throttle collapses a boundary that resolves twice into one beep.

**Keyboard shortcuts** (`shortcuts/` + `utils/keyboard.ts`): `shortcuts/defaults.ts` is the catalogue (id · label · context · default key · rebindable); `shortcuts/store.svelte.ts` holds user overrides (localStorage `insp_seg_shortcuts`, runes `$state`) and the `resolve(token, context)` reverse lookup. `handleSegmentsKey` picks a context — `edit` (trim/split: ←/→ stepper, Tab cycles cursor/region, Enter/Escape), `accordion` (a validation accordion is open: ↑/↓ + autoplay walk the `accordion-nav` sequence, G/C/L/F act on the focused card), or `default` — then dispatches the resolved action. Row/card edit actions (A/S/E/G/L/F/C) route through the `active-actions` registry, which the primary SegmentRow publishes; accordion cards forward their ignore/auto-fill/toggle-context callbacks via SegmentRow's `onCard*` props. The footer `ShortcutsGuide.svelte` (left of the speed control) is the reference + inline rebinder; the old reference accordion in `GuidesGateModal` is gone.

**Validation accordion sorts** (`domain/sorting.ts` + `stores/validation-sort.ts`): a declarative, FE-only sort abstraction. Each registry row declares the sort options it offers via `IssueDefinition.sorts` (e.g. Low Confidence → `confidence` (default) + `quran_order`; Missing Words → `quran_order` + `word_count`; Cross-verse → `quran_order` + `verse_count`; Repetitions → `quran_order` + `rep_split_count`); Missing Verses and the non-registry Flagged accordion offer none. `sorting.ts` owns the key extractors (`quran_order` reads `(chapter, seg_index)`, falling back to `verse_key`/`ref`; counts read the relevant field; `rep_split_count` reads `refs.length` from the auto-split map) and `sortItems`, which orders by the active `(kind, dir)` and breaks ties with the *other* offered option ascending. `ValidationPanel` renders one pill per option in the accordion header — click to select, re-click the active pill to flip ▲/▼ — and applies the sort in `projectVisible`. Choice + direction persist globally (not per-reciter) in `valSortPrefs`; `resolveSort` falls back to the registry default and rejects a stored kind a category no longer offers. The `sorts` field is deliberately absent from the Python registry and its parity snapshot.

## Hybrid imperative canvas pattern

Canvas waveform components expose the raw `<canvas>` so overlay code draws directly each animation frame, avoiding reactive re-renders at 60 fps. Drivers:
- `tabs/segments/utils/playback/playback.ts` (playhead overlay)
- `tabs/segments/utils/waveform/{split-draw,trim-draw}.ts` (edit overlays)
- `TimestampsTab.svelte` / `TimestampsWaveform.svelte` (waveform animation)

These components stay Svelte-4 legacy by design (`docs/planning/svelte-migration.md` exempt list). `lib/utils/animation.ts::createAnimationLoop()` wraps the rAF lifecycle.

## Client caching

| Cache | Location | Invalidation |
|---|---|---|
| Segment peaks | `tabs/segments/utils/waveform/peaks-cache.ts` (lazy via IntersectionObserver) | reciter/chapter change |
| Audio peaks (cross-tab) | `lib/utils/waveform-cache.ts` (Map keyed by normalized URL) | manual |
| Quran refs bundle | `lib/refs/quran-refs.ts` (one content-hashed fetch/browser) | content hash (never per-session) |
| Catalog list + stats | `tabs/dashboard/stores/catalog-data.ts` (in-memory snapshot) | first read only |
| Audio transport/prewarm | per-tab `AudioPort` + `lib/playback/shadow-audio.ts` | tab-switch / chapter change |

## Build outputs

`frontend/dist/` is gitignored. `npm run build` = `tsc --noEmit && vite build` → hashed JS + CSS, Chart.js in a separate `charts` chunk. Run before launching Flask in production mode.


## Multi-riwayah

`lib/riwayat.ts` mirrors the backend slug table and is typed off the codegen'd
union, so a backend change that adds a riwayah breaks `npm run check` rather
than shipping a stale list. Two vocabularies meet in the browser: the shard's
`_meta.riwayah` is the **SDK** slug (`warsh`), while the font and reference
routes are keyed by the **Inspector** slug (`warsh_an_nafi`) —
`toInspectorSlug()` is the bridge.

- `lib/refs/quran-refs.ts` — the per-edition reference bundle. One edition in the
  store at a time, cleared on switch, one bundle in `sessionStorage`.
- `lib/refs/edition-font.ts` — `--font-quran` per edition; Hafs keeps the bundled
  Digital Khatt stack (its font is inlined because HF Spaces do not smudge
  Git-LFS at build time).
- `tabs/timestamps/stores/display.ts` — `wordProfile` + `deliveryRiwayah`, set
  from the **shard**, and the derived `lettersVisible` / `phonemesVisible` /
  `wipeActive` / `effectiveGranularity` that every view renders from. The raw
  writables stay the user's persisted preference.
- `tabs/timestamps/components/WordTimedRow.svelte` — the word-profile analysis
  row, reusing the `.timed-analysis` class so every `--qc-*` token and
  report-mode rule applies unchanged.
- Shared `lib/` components must not import a tab store: `NowReciting.svelte`
  locks granularity off the DATA (`units.some(u => u.letters.length)`), not off
  the delivery's riwayah.

Full detail: [`editions.md`](editions.md).
