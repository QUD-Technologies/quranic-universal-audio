# Frontend playback

The single `<audio>` element in each surface is wrapped in an `AudioPort`. The port mediates transport (CBR vs VBR), translates coordinates (file-absolute ↔ clip-relative), and routes through a Web Audio kill-switch graph for sample-accurate pause silencing. Warmup primes the decoder + AudioContext on the first user gesture. `AudioRange` enforces playback windows. Look-ahead warming is split: `warmup.ts` (CBR HTTP-Range) for Segments and a hidden **element pool** (`shadow-audio.ts`) for gapless cross-source jumps.

For VBR-specific port behavior see `vbr.md`.

> **Three corrections vs old docs:** (1) There is **no `prefetch.ts` util, no `prefetchEnabled` store, no `insp_seg_prefetch` key** — replaced by `warmup.ts` + the shadow-audio element pool. (2) **Timestamps + Dashboard both run on the shared `dashPort`** via `BottomPlayer`; there is **no `audPort`/audio tab**, and `tsPort` is defined but **unused** (vestigial). (3) `installAudioWarmup()` runs in **`main.ts`**, not `App.svelte`.

## The ports

| Port | Module | Drives | Kill-switch |
|---|---|---|---|
| `segPort` | `tabs/segments/stores/playback.ts` (`new AudioPort()`) | Segments tab | enabled (proxied same-origin URLs) |
| `dashPort` | `lib/playback/dash-port.ts` | **Dashboard AND Timestamps** (via `BottomPlayer`); supports `adoptElement` gapless swaps; `TimestampsWaveform` reads `dashPort.currentTimeMs()` for karaoke | `disableKillSwitch: true` (some sources are raw cross-origin links, not proxied) |
| `tsPort` | `tabs/timestamps/stores/playback.ts` | nothing — vestigial; delete or rewire | — |
| per-panel | `tabs/segments/utils/playback/preview.ts` (`new AudioPort()`) | SavePreview / HistoryPanel, isolated from live `playingSegmentIndex` | enabled |

All module-scoped so identity survives HMR; components only `attachElement` on mount.

## AudioPort

`lib/playback/audio-port.ts` (~694 lines). Single transport chokepoint.

### Lifecycle / API

```ts
const port = new AudioPort();                         // bare; defaultPadMs=0, kill-switch on
port.attachElement(audioEl);                          // bind <audio>
port.adoptElement(el, srcUrl);                        // NEW — transplant a pre-warmed element (gapless)
port.setSource({ audioUrl, cbrSrc, reciter, vbr });   // bind logical source
const { ready, swapped, window } = port.loadCovering(startMs, endMs, pad?);
await ready;                                          // resolves on canplay
port.covers(startMs, endMs);                          // NEW — does the loaded window already cover [s,e]?
port.prewarm();                                       // NEW — kick a load without seeking/playing
port.seekAndPlay(fileMs);
port.play();                                          // NEW — resume without re-seeking
port.pauseAndFlush();                                 // pause + Web Audio gain ramp to 0
port.dispose();
```

Subscriber API (each returns an unsubscribe fn, snapshotted before fanout): `onLoad`, `onPlay`, `onPause`, `onEnded`, `onTimeUpdate` (file-absolute), `onError`, `onWaiting` (network/decoder starvation), `onPlaying` (actual audible resume, distinct from `onPlay`).

### Coordinate contract

**File-absolute milliseconds outside the port. Clip-relative inside.** Every consumer hands the port file-absolute ms; the port subtracts `_window.offsetMs` before writing `el.currentTime` (`seek()`). `offsetMs` is `0` for CBR (chapter URL, file-absolute currentTime), equal to clip start for VBR (segment-clip URL plays from byte 0). Helpers: `toClipMs`, `toFileMs`, `currentTimeMs()` (file-absolute), `coordinates()` (`{offsetMs}`).

**Bug class — directly writing `el.currentTime`:** any consumer mutating `audio.currentTime` outside the port is wrong; in VBR mode it bypasses offset translation and lands the playhead in the wrong file-absolute position. The only legitimate live writes are inside the port, legacy `AudioRange` element-mode, and `shadow-audio.ts` (pre-roll seek on a hidden element). The two in `AudioPlayer.svelte` are legacy Svelte-4 survivors — flag for migration.

### Coverage / loading

`loadCovering(startMs, endMs, pad?)` is idempotent — returns sync-resolved `ready` with `swapped:false` when `_window` already covers the request and matches the desired transport. Otherwise swaps via `_swapTo(url, win)`, sets `_window` **synchronously** (the moment the swap starts, not when canplay fires), attaches a one-shot `canplay`, resolves `ready` on canplay.

- **Load-gen counter:** `loadGen` bumps on every swap; pending canplay handlers compare gen and silently drop on mismatch — a newer `loadCovering` cancels an older one's promise without a race.
- **Pending-promise reuse:** a follow-up `loadCovering` during a swap whose in-flight target window covers the new request gets the same `pendingPromise` with `swapped:true` — caller awaits canplay instead of seeking into a still-loading element.
- **Sync `_window` write:** prevents the VBR Adjust-mode bug where an immediate split-click after enter would seek/play against stale `offsetMs` before the wider clip's canplay landed.

### Source binding

`setSource(src)` is identity-keyed on `(audioUrl, reciter, vbr)`. Same triple = no-op. Different triple aborts pending load, clears `_window`, updates `_source`. `null` clears src + calls `el.load()`.

### Cross-origin gotcha (`disableKillSwitch`)

`MediaElementAudioSourceNode` requires CORS to read samples; constructing one against a cross-origin URL without CORS silently mutes (Web Audio spec). **The audio-proxy now streams same-origin with `ACAO: *`** (`routes/audio/proxy.py`), so `crossorigin="anonymous"` elements playing **proxied** URLs route through Web Audio fine — the foot-gun is mostly defused. `disableKillSwitch: true` (skips Web Audio entirely; the audible-tail bug at pause returns) is now needed only for **raw cross-origin URLs not wrapped by the proxy**. Wrap CBR by_surah URLs via `wrapCbrSrcIfBySurah` (`source.ts`) and keep the kill-switch.

## AudioGraph (Web Audio kill-switch)

`lib/playback/audio-graph.ts`. Routes `<audio>` → `MediaElementAudioSourceNode → GainNode → ctx.destination`.

**Why:** `HTMLAudioElement.pause()` halts the source renderer but doesn't flush samples already in the OS device (WASAPI shared mode 50–200 ms, up to 300 ms Bluetooth) — an audible "tail" past `time_end`. `audio.muted`/`volume=0` are source-side and don't silence the queue. Web Audio applies gain at the render quantum, **before** the platform sink; a 5 ms `linearRampToValueAtTime(0)` drops tail to ~`outputLatency`, avoiding click-pop.

```ts
_getCtx()                     // exported for warmup/probing; AudioContext | null
getAudioGraph(el)             // null while ctx suspended; kicks ctx.resume()
ensureAudioContextRunning()   // await before play after a tab switch
cutAudio(el) / uncutAudio(el) // 5 ms ramp to 0 / 1
getOutputLatencyMs()          // (baseLatency+outputLatency)*1000; 0 when ctx null/suspended
displayTimeMs(mediaMs)        // mediaMs − output latency, clamped ≥0 — DISPLAY-ONLY playhead comp
```

**Visual latency compensation (`displayTimeMs`).** `el.currentTime` is the decode/media clock; it leads the audible output by the platform output latency (`baseLatency+outputLatency`, ~20–30 ms wired, 100–300 ms BT). Two display consumers compensate with `displayTimeMs(time)`: the Segments waveform playhead (`playback.ts::drawActivePlayhead`, clamped into `[seg.time_start, seg.time_end]` — the single chokepoint for both continuous + bounded draw paths) AND the footer progress bar + elapsed clock (`SegmentsFooter.svelte`, `currentMs = displayTimeMs(fileMs)` in the `onTimeUpdate` sub). Keep them in lockstep — compensating only one makes the bar and cursor disagree (issue #172). **Display-only:** never feed it to `seek`/AudioRange boundary/`onSegTimeUpdate` (those use the raw clock). `getOutputLatencyMs` caches the last non-zero reading, so a momentary 0 (ctx suspended / first play before the graph is built / a browser that reports 0) degrades to the last good estimate rather than snapping back to uncompensated. Segments-only today; Timestamps karaoke playhead could adopt the same helper.

**Two invariants:**
- **Graph build is gated on `ctx.state === 'running'`.** `getAudioGraph` returns `null` while suspended — building a `MediaElementAudioSourceNode` against a suspended context redirects output to a silent graph. So the kill-switch silently no-ops on the very first play (warmup may still be resuming); subsequent plays get it.
- **`MediaElementAudioSourceNode` is once-per-element for the page lifetime** (Web Audio spec) — cached via `WeakMap<HTMLAudioElement, AudioGraph>`. **This is why `shadow-audio.ts` rotates elements** rather than swapping `src`: the graph travels with the element.

## Audio warmup

`lib/utils/audio-warmup.ts`. One-shot `installAudioWarmup()` (called in **`main.ts`**) registers `pointerdown`/`keydown`/`touchstart` (`once:true, capture:true`). On first gesture: (1) play+pause a ~250-byte silent MP3 data URI on a throwaway `Audio()` to warm the decoder + acquire the OS audio device (first real `.play()` then skips the 200–1000 ms cold start); (2) `_getCtx().resume()` inside the gesture handler (Chrome requires a gesture). `_warmed` flag makes it idempotent.

## AudioRange

`lib/playback/audio-range.ts`. `[startMs, endMs]` window with pluggable boundary policies (`stop`/`loop`/`advance`). rAF-driven; auto-pauses at `endMs`. Frame-bound (~16 ms), not audio-clock-locked.

- **`attach()` (NEW):** start the rAF watcher without seeking/playing.
- **Live-element reads:** `stop()`/`_frame()` read the **live** `port.element`/`port.paused`, so boundary enforcement survives `adoptElement` element rotation (the gapless path).
- Gap-and-advance: on a boundary, pause → wait `gapMs` → load next spec → play. Inherent pause between segments; no crossfade.
- **Port-mode preferred** (uses `AudioPort` for src + offset bookkeeping). Legacy audio-element mode (`audioEl` arg, `clipFileOffsetMs`) retained transitionally — new surfaces must use port mode.

## Gapless: element-pool adopt

For cross-source jumps where the once-per-element constraint forbids re-using one element (TS shuffle / auto-advance):

- `lib/playback/shadow-audio.ts` — warm a hidden `<audio>` element (`shadowPrewarm(url, {slot, seekSec})`, slots `'any'`/`'shuf'`), then `consumeWarm(url)` hands it over and `recycleAsShadow(el, slot)` returns the old one to the pool.
- `lib/playback/shuffle-prewarm.ts` — thin wrapper remembering which `TsRandomTarget` a warm URL belongs to (`primeShuffle`/`consumeShuffle`/`clearShuffle`). Slot `'shuf'`.
- `lib/playback/dash-prewarm.ts` — the **dashboard intent-driven look-ahead** (slot `'dash'`, depth 1). Two tiers: `primeDashSpeculative(url, seekSec)` (range-windowed warm for hovers — next/prev button, surah popover, progress bar, filmstrip cell, scrub-settle) and `primeDashCommitted(target)` + `consumeDashCommitted(url)` (full-element warm for the imminent gapless-next chapter, adopted on `dashPort.onEnded`). `clearDashPrewarm()` cancels on reciter/chapter switch. Wired from `BottomPlayer.svelte` (+ `NowReciting`/`AyahFilmstrip` for the filmstrip-hover hook). A committed target outranks speculative hovers (single slot).
- `lib/playback/adopt-signal.ts` — module-level single-shot signal so `BottomPlayer.reactToContext` skips a reload after a gapless adopt (`setAdoptedSource`/`takeAdoptedSource`). Used by BOTH the TS shuffle adopt and the dashboard gapless-next adopt.
- `port.adoptElement(el, srcUrl)` transplants the warm element; the kill-switch graph rides along on the element.

## Segment look-ahead warming (CBR)

`tabs/segments/utils/playback/warmup.ts` — `warmSeg(...)` / `warmChapterStart(...)`: a **64 KB HTTP Range** fetch at the segment's byte offset (using `chapter_bitrate_kbps_for_reciter` to compute the offset), 30 s dedupe, fire-and-forget. Replaces the deleted `prefetchNextSegAudio`. The two "next" resolvers survive in `resolvers.ts` (`nextDisplayedSeg`, `nextSiblingSeg`).

## Peaks rendering (FE)

- Read via `getWaveformPeaks(url)` (`lib/utils/waveform-cache.ts`, URL-normalized). Segments fetches via `_fetchPeaks` (`tabs/segments/utils/waveform/utils.ts`); cross-tab/TS via `ensureChapterPeaks` + `pickChapterPeaks` then `fetchSegmentPeaks` fallback (`lib/utils/peaks-fetch.ts`).
- **Decode wire b64 only via the shared `b64ToInt8`** (`lib/utils/peaks-decode.ts`) — one signed-reinterpret implementation so Segments and Timestamps can't drift on byte interpretation.
- **Always wrap in `viewPeaks(...)` (`lib/utils/peaks-view.ts`) before drawing** — a shape adapter exposing `length / min(i) / max(i)` over either an `Int8Array` (Tier-1 / history) or a nested `PeakBucket[]` (Tier-2 ffmpeg fallback). The drawer (`waveform-draw.ts`, `draw-seg.ts::_slicePeaks`) branches on shape **once** at view construction; per-pixel reads are shape-free. Per-segment slicing is a zero-copy `subarray`.
- **History peaks are now `Int8Array`** (decoded on receive via `indexHistoryPeaksRecords`) — the ~3 MB `PeakBucket[]` heap regret now applies **only** to the Tier-2 ffmpeg `/segment-peaks` fallback.

## Stores (`tabs/segments/stores/playback.ts`)

- `segPort` (module-scoped), `segPortReady`, `activeAudioSource`, `playingSegmentIndex`+`setPlayingSegment`, `isMainAudioPlaying`, `playButtonLabel`.
- `autoPlayEnabled`, `autoScrollEnabled`, `playbackSpeed` — persisted as `insp_seg_autoplay` / `insp_seg_autoscroll` / `insp_seg_speed`.
- **No `prefetchEnabled` / `insp_seg_prefetch`.** Diagnostic: `localStorage insp_warmup_log='true'` enables prewarm/shadow trace logs.

## Test surface

`lib/playback/__tests__/`: `audio-port.test.ts`, `audio-graph.test.ts`, `audio-range.test.ts`, `audio-range-port.test.ts`, `raf-harness.ts`. `tabs/segments/utils/playback/__tests__/`: `preview.test.ts`, `range-spec.test.ts`, `warmup.test.ts` (no `prefetch.test.ts`).

## Feature-building seams

- **New AudioPort consumer:** import/construct a module-scoped port, `attachElement` on mount, `setSource` → `loadCovering` → `await ready` → `seekAndPlay` (file-absolute ms). Gapless cross-source → prewarm via `shadow-audio.ts` + `adoptElement`.
- **Shared loading spinner (cross-tab):** `lib/components/player/LoadingSpinner.svelte` (Svelte 5 runes; props `size`, `color`, default `currentColor`) is the ONE audio-loading indicator — a ring with a brighter top arc, 0.7s linear (1.4s under reduced-motion). Used by the Segments footer play button (`{#if $segAudioBuffering}`), Dashboard + Timestamps shared transport (`PlayerControls.svelte`, `{#if isLoading}`). Purely presentational — no port/store knowledge; mount it while the owning player's buffering state is true. Replaces the old inline `.play-spinner` (Segments) and the `.btn.primary.loading::before` `player-ring-pulse` (Dashboard) — both deleted.
- **Dashboard + Timestamps buffering:** `lib/playback/dash-buffering.ts::installDashBuffering(setLoading)` is the SINGLE owner of `playerContext.isLoading` for the shared `dashPort`. Raises on `waiting` / `signalDashSeekIntent()` (debounced 120 ms so in-buffer seeks don't flicker), clears on `onPlaying` (first audible frame) / pause / ended / error. `BottomPlayer` installs it on mount + calls `signalDashSeekIntent()` at every seek/play/source-change; **Timestamps** must also call `signalDashSeekIntent()` at its direct `dashPort.seek()` sites (`seekFocus`/`seekMsAndResume`/`syncChapter` pendingSeek in `TimestampsTab.svelte`, `onCanvasClick` in `TimestampsWaveform.svelte`) so verse-click/keyboard-nav/drag/shuffle all show the spinner. Driven off port events → tracks across `adoptElement` rotation; never gate on `onLoad`/canplay (readyState 3 ≠ audible). A paused chapter-select fires `dashPort.prewarm()` so the play-click isn't a cold start. **Scroll/wheel in Timestamps is zoom, not seek — correctly no spinner.**
- **New intent-prewarm signal (dashboard):** call `primeDashSpeculative` (hover) or `primeDashCommitted` (committed) from `dash-prewarm.ts`; keep depth 1 (single `'dash'` slot) and `clearDashPrewarm()` on source switch so a stale warm can't adopt.
- **New peaks consumer:** `getWaveformPeaks(url)` → `viewPeaks(...)` → draw. Decode wire only via `b64ToInt8`. Never branch on peaks shape at the call site.
- **New boundary policy:** extend the `RangePolicy` union + add a `case` in `_handleBoundary()`; honor the live-element reads so adopt rotation doesn't break it.
- **New tab's playback:** mirror `dashPort`/`segPort` — module-scoped port in a `stores/playback.ts`, component `attachElement`s a `<audio crossorigin="anonymous">`, a `*PortReady` writable, App-level tab-switch `port.pause()`. Keep the kill-switch on unless it plays raw cross-origin audio.

## Stale comments in live code (fix on sight)

- `peaks-view.ts` claims the route passes `?shape=i8` — false; the route emits int8 verbatim, no query param.
- `resolvers.ts` docstring says "shared with prefetch" — prefetch is deleted; only `range-spec` + warmup use it.
- `audio-range.ts` references a "final cleanup phase" — refactor-narrative comment.
