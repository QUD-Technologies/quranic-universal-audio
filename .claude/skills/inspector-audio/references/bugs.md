# Common bug shapes

Symptom → likely root → first probe. Ordered by area, not severity. VBR-only shapes live in `vbr.md`; this page covers the rest plus the highest-traffic VBR ones for completeness.

## Playback won't start

| Symptom | Likely root | First probe |
|---|---|---|
| First play takes ~800 ms even on cached audio | Warmup didn't fire — no user gesture before first programmatic play, or `installAudioWarmup` not called at app boot | DevTools console: check `_warmed` flag in `audio-warmup.ts`. Confirm `installAudioWarmup()` runs in **`main.ts`** (not `App.svelte`). |
| `<audio>.play()` rejected with NotAllowedError | First play happened before user gesture — autoplay policy | Check call stack — first play must happen inside a click/keydown/touchstart handler. Move it. |
| Clicking play does nothing, no error | Source not bound — `setSource` not called or called with `null` | `port.source` should be non-null. Check the source-binding callsite. |
| Audio element's src is empty after `loadCovering` | `_swapTo` failed to assign — `el` was null mid-call | `port.element` returned `null` — `attachElement(audioEl)` not called or `audioEl` not yet mounted. |

## Playback starts but no audio

| Symptom | Likely root | First probe |
|---|---|---|
| "Progress moves but no audio" after tab switch | AudioContext suspended on tab background, not resumed before next play | `_getCtx().state` is `suspended`. Call `await ensureAudioContextRunning()` before play. `audio-graph.ts:106-118`. |
| Silent playback after first cross-origin chapter src swap | `MediaElementAudioSourceNode` constructed against a **raw** cross-origin URL without CORS — Web Audio spec mutes. (Mostly defused: the audio-proxy now streams same-origin + `ACAO:*`, so **proxied** URLs route through Web Audio fine. Only raw cross-origin / the 302-fallback `dashPort` need the opt-out.) | Use `disableKillSwitch: true` only for ports playing raw cross-origin sources (see `dashPort`); otherwise wrap via `wrapCbrSrcIfBySurah`. |
| Silent only on first play, audio on subsequent | Kill-switch graph built while ctx was still suspended (warmup didn't resume in time) | Should not happen — `getAudioGraph` returns `null` while ctx suspended. If it does, warmup gesture binding is broken. |
| Audio plays from prior chapter briefly after switching | `setSource` not called before `loadCovering`, or pending-load gen race | `setSource(...)` aborts pending load + clears `_window`. Check the order of calls. `audio-port.ts` (`setSource` ~`:244-256`). |

## Audible artifacts

| Symptom | Likely root | First probe |
|---|---|---|
| Audible audio after pause (50–300 ms tail) | Web Audio graph not constructed (ctx still suspended), kill-switch silently no-op'd | Console: `_getCtx().state` should be `running`. Confirm warmup fired before play. |
| Click/pop at pause | Kill-switch ramp duration too short, or gain was already 0 | Default 5 ms ramp. Check `cutAudio` not called twice. |
| Glitch at clip boundary in advance mode | `endMs` clip end didn't have post-roll pad — boundary flush has no audio to drain | Bump `defaultPadMs` on the AudioPort instance. VBR-specific — see `vbr.md`. |

## Playhead / progress runs AHEAD of the audio

| Symptom | Likely root | First probe |
|---|---|---|
| Waveform cursor and/or footer progress bar lead the recitation by a constant offset ("looks like the start should be cropped"); only on SOME clients (e.g. Brave/Edge laptop), maintainer can't repro | `el.currentTime` is the decode/render clock and leads the AUDIBLE sink by the platform output latency (Web Audio graph buffer + OS sink, ~20–30 ms wired, 50–300 ms on some stacks/BT). The rAF cursor + footer track `currentTime`, so they lead by that latency. Client-audio-stack dependent → not reproducible on a low-latency setup. NOT network/region (a slow network makes the cursor WAIT on a pinned `currentTime`, it can't lead). | Reporter console: `_getCtx()?.outputLatency`, `_getCtx()?.baseLatency`, `_getCtx()?.state`. Enable `localStorage.insp_warmup_log='true'` → `[play] FIRST audible frame …ms` gap. |
| The compensation (`displayTimeMs`) doesn't fix it | (a) The **footer** bar/elapsed must also be compensated — it reads the raw clock (`SegmentsFooter.svelte` `currentMs = displayTimeMs(fileMs)`), the cursor is done in `playback.ts::drawActivePlayhead`. (b) `getOutputLatencyMs` falls back to a cached non-zero reading only when ctx is `null`. The cache is **inert for the warm `outputLatency=0` window** it was written for: real hardware always reports `baseLatency≈0.01`, so `(base+output)*1000 > 0` even when `output==0`, and the function returns/overwrites with the live under-compensated value instead of the cached one (the #184 unit test passes only because it sets `baseLatency=0` too, which hardware never does). To bridge the warm-up window, cache on `outputLatency>0`, not on the sum. (c) Output latency is ~50 ms here — sub-perceptual. If the perceived lead is large, it's the **startup gap** (next row), not output latency. | `audio-graph.ts::getOutputLatencyMs` / `displayTimeMs`; confirm BOTH `drawActivePlayhead` and the footer `onTimeUpdate` apply it. |
| Bar/cursor reach a position the audio hasn't, esp. at play start ("the start is cropped") — the dominant real #172 | **Click→first-audible gap**, not steady-state latency. On click `playFromSegment` flips the button to pause + synchronously seeks `el.currentTime` to `seg.time_start`; the first audible frame (`playing` event) lands 0.3–3 s later (cold Range fetch / re-buffer at the seek target). During the gap the UI signals "playing" while silent. The footer bar ALSO snaps to the destination IF the seek's `timeupdate` fires synchronously (target already buffered — common on short fully-buffered chapters). Gap scales with network + how much of the chapter is buffered, NOT with output latency, so `displayTimeMs` (~50 ms) can't touch it. | Throttle CDP to Slow 3G, play a far segment of a LONG chapter (Al-Baqara/An-Nisaa won't fully buffer); record `play`→`playing` delta + sample footer `.fill` width each rAF. Fix: `segAudioBuffering` store (`stores/playback.ts`) true from play-commit→`playing`, drives a play-button spinner AND gates the footer `currentMs` write (`if (!get(segAudioBuffering)) currentMs = displayTimeMs(fileMs)`) so the bar holds the audible position through the gap. |

## VBR seek (see `vbr.md` for full coverage)

| Symptom | Likely root | First probe |
|---|---|---|
| VBR seek lands seconds off, all modes | Xing TOC missing — Katana's `audio_persist::_ensure_xing` didn't inject it (raw VBR shipped) | `ffmpeg -i reciters/<slug>/audio/<ch>.mp3 2>&1 \| grep -iE 'xing\|info'`. If absent, re-extract the chapter — there is no Space-side remux to retrigger. |
| VBR seek lands off only in Adjust mode | `_window` not yet updated when split-click fires | Check `audio-port.ts:342-349` pending-promise reuse — should hand back the in-flight promise. |

## Bucket audio + peaks (offline-written)

> There is **no prefetch worker and no GC sweeper** anymore — **Katana extraction is the sole writer** (there is no separate "timestamps job" peaks pass; that's a phantom in old docs); the Inspector only reads, and nothing is GC'd. See `prefetch.md`. "Audio missing on the bucket" is an extraction/upload problem, not a Space-side fetch problem.

| Symptom | Likely root | First probe |
|---|---|---|
| Audio/peaks never appear on bucket for a slug | The align run's acquire / split stage didn't finish | Check the run in Admin → Requests (`GET …/align/status`); retry the run. |

## Audio proxy / serving

| Symptom | Likely root | First probe |
|---|---|---|
| Slow first byte on play (CDN stream-through) | Bucket not mounted in this env / chapter not on bucket → tier-3 `_stream_cdn` proxies from the CDN (200/206 same-origin, **not** a 302) | `audio_source.resolve` falls through to CDN; check `audio_fetch.read_prefetched_audio_local_path` returns `None`. |
| Range request returns 200 not 206 | Server-side `send_file(conditional=False)` somehow, or BytesIO wrap dropped Range support | `routes/audio/proxy.py` should always be conditional. |
| ETag changes every request | `last_modified` based on bucket file mtime that changes per read (mount issue) | Use `chapter_key + length` ETag fallback path. |
| Cross-origin CORS failure on segment-clip | `Access-Control-Allow-Origin: *` header dropped | `routes/audio/clip.py`. Check no middleware strips it. |
| Segment-clip 403 | `_is_known_chapter_url` rejected — URL not in the **manifest sidecar** | Confirm `audio_meta.chapter_for_url(reciter, url)` resolves (NOT `detailed.json` — its `audio` field is `""`). |
| Segment-clip 200 / 0 bytes | ffmpeg cmd missing `-vn` → stripped ffmpeg chokes on embedded APIC cover-art (no png encoder) | Confirm `routes/audio/clip.py` cmd has `-vn`. |
| Segments footer progress bar blank for ONE reciter (audio still plays) | Chapter audio not on the bucket → `_stream_cdn` serves a headerless MP3 → browser reports `el.duration` Infinity/NaN. The footer hides the bar when duration isn't finite. (Footer now listens for late `durationchange` + falls back to `segAllData.chapter_duration_ms_by_chapter`; also fix the data: `populate_bucket_audio.py --slug <s> --bucket prod`.) | `scripts/bucket/bucket_reciters.py --bucket prod` → compare `audio_n` vs manifest chapters. FE: `SegmentsFooter.svelte` + `footer-duration.ts::resolveChapterDurationMs`. |
| One chapter seconds-to-minutes out of sync in BOTH Segments and Timestamps; bucket file and segments agree (e.g. husary mujawwad tarteel ch 62) | The source CDN replaced the file under the same URL with a different recording; direct CDN play skipped the bucket copy the delivery was aligned on. `play-url.ts` now plays direct only when the live size equals the manifest `size_bytes` (`Content-Range` total, else a 1-byte read at `size-1`); unsized or mismatched chapters stay proxied. | `curl -sI <cdn url>` Content-Length vs `catalog/audio_manifest/<slug>.json` `size_bytes`; `ffprobe` both copies. In the browser the `<audio>` src is the raw CDN URL instead of `/api/seg/audio-proxy/...`. |

## Peaks

| Symptom | Likely root | First probe |
|---|---|---|
| Peaks render but stale | `?h=` hash didn't change after edit — frontend hash function missed boundary mutation | Inspect peaks request URL in network tab — hash must change when boundaries change. |
| Peaks shifted on a Tier-2 segment | ffmpeg `-ss` seek imprecision on the segment decode (the old Python `_range_decode_segment` / `bytes_per_sec` arithmetic is **gone** — ffmpeg is frame-aware now) | `compute_segment_peaks` in `services/audio/peaks.py`. Re-run the ffmpeg `-ss/-t` cmd manually against the source. |
| `/peaks` slow on first hit | Bucket short-circuit didn't fire — URL not in sidecar (`chapter_for_url` returned None) | Manifest sidecar missing or stale. Re-run `scripts/audio/probe_audio_meta.py`. |
| One URL returns null peaks | `read_prefetched_peaks` returned None (corrupt / pre-v3 / never extracted) — no runtime re-bake | Check `routes/segments/peaks.py` log. Backfill or re-extract; falls through to Tier-2 ffmpeg. |
| Peaks recompute every request | in-memory cache cleared (process restart), or the per-URL `_PEAKS_CACHE` not populated | First readers fan out (`ThreadPoolExecutor`, `get_peaks_lock()` — NOT a phantom `is_peaks_computing`); subsequent hit `_PEAKS_RESPONSE_CACHE`. Check it's populated after the first request. |

## Coordinate / state

| Symptom | Likely root | First probe |
|---|---|---|
| Cross-verse compound segment plays wrong audio | Wrong `audio_url` selected by the segment resolver — clip URL builds against wrong chapter | Trace `vbrClipForChapter` and `Segment.audio_url` populated from `detailed.json`. |
| Seek lands at file-relative when caller expected file-absolute | Caller wrote `el.currentTime` directly | Find the offending callsite via grep. All seeks must go through the port. |
| Time updates show wrong file-absolute ms | `_window.offsetMs` stale (wasn't updated after a swap) | `_window` is set synchronously in `_swapTo` (`audio-port.ts:591-630`, `this._window = win` at `:605`). If stale, a custom load path is bypassing the port. |

## Dataset publish — offline per-verse slicing (`qua_jobs/publish_hf.py`)

The HF dataset publish job cuts ~6,235 per-verse clips from each chapter MP3 via **in-process MP3 frame-index slicing** (`qua_shared/mp3_frames.py`), NOT ffmpeg. `build_frame_index(data)` parses the chapter's frame grid once (skips ID3 + Xing/Info/VBRI); `slice_frames` copies the byte range of the frames covering `[clip_start, clip_end]`, snapping the start to the frame boundary ≤ clip_start. `_rebase_row` shifts clip-relative word/letter/segment times by `clip_start - actual_start_ms`.

| Symptom | Likely root | First probe |
|---|---|---|
| Published verse audio clipped at the onset (~50 ms missing) | Pre-rewrite ffmpeg `-ss` overshot the seek by 1–2 frames AND the old `snap = actual_dur − requested_dur` heuristic assumed a *backward* snap that never happened → rebase delta wrong in sign. **Fixed** by the frame-index slicer (true frame ≤ clip_start). | Compare `actual_start_ms` from `slice_frames` vs the source frame ffmpeg's first audio frame lands on (decode both, find ff PCM inside the wider clip). |
| Every clip duration off by ~1 frame vs old output | Expected — frame-index window is `[≤start, ≥end]` (correct superset); ffmpeg's was a forward-shifted window. Audio frames are byte-identical where they overlap. | `slice_frames` duration must be within ±1 MP3 frame of the ffmpeg `-c copy` clip; PCM of the overlap is bit-identical. |
| Slicing slow / oversubscribed on small flavor | Worker pool sized from `os.cpu_count()` (host cores) not the cgroup quota → 6× oversubscription on 2-vCPU. **Fixed** via `_slice_workers()` reading `/sys/fs/cgroup/cpu.max`. | Log line "slicing N rows … with K workers" — K should be ≈ vCPU+1, not host cores. |
| VBR verse mistimed in the dataset | Frame index used a fixed per-frame duration instead of reading each frame header — desyncs on Xing-injected VBR. `mp3_frames` reads bitrate/sr per frame; the Xing/Info/VBRI header frame is skipped. | `build_frame_index(data).n_frames` vs `ffprobe -show_packets` frame count; durations must match. |

## Adding a new bug shape

When investigating a bug not covered here, append a row in the right section after diagnosis. Keep entries to one symptom-line + one root-line + one probe-line. If the new bug is VBR-only, add it to `vbr.md` instead.
