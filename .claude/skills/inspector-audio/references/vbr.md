# VBR (variable bitrate) handling

VBR-MP3 is the single largest source of correctness bugs in the audio subsystem. This page is the consolidated reference for everything that exists *because* of VBR.

## Why VBR is hard

CBR-MP3 has a constant byte-rate, so `byte = bytes_per_sec * t` lets any client (browser, ffmpeg, byte-range-aware backend) seek to a time by computing a byte offset. VBR breaks that — the byte ↔ time map is non-linear and recoverable only via the **Xing TOC** header at the start of the file.

Without a Xing header the browser does not know where to land for `audio.currentTime = t`, so it linearly extrapolates from `bytes_per_sec` and lands seconds (sometimes tens of seconds) off. This is the canonical "VBR seek drift" symptom.

## The two-pronged fix

The subsystem fights VBR on two fronts:

1. **Inject the Xing TOC at extraction time** so the browser-side seek path Just Works for the chapter MP3 on the bucket. Done offline on Katana — `.local/extraction/segments/audio_persist.py::_ensure_xing`.
2. **Route per-segment playback through a server-side clip endpoint** so we don't need browser-side seeks at all. `routes/audio/clip.py` + `lib/playback/audio-port.ts` (VBR branch).

Both exist because (1) only covers chapters Katana has extracted + uploaded to the bucket; (2) covers the gap (anonymous browsing, pre-extraction onboarding, by_ayah deliveries served straight from the CDN).

## Xing/Info header injection

`.local/extraction/segments/audio_persist.py::_ensure_xing` (+ `_force_info_if_cbr`). Runs **offline on Katana**, not in the Space — there is no in-Flask remux anymore. Applied to every by_surah chapter:

```
ffmpeg -y -i <src> -c:a copy -f mp3 -v error <dest>   # then: Xing -> Info if CBR
```

`-c:a copy` means no re-encode. The trick is `-f mp3`: ffmpeg's mp3 **muxer** writes a Xing/Info header (Frames count + TOC + bytes) at file start whenever the output is seekable. No `-bsf:a mp3_to_xing` bitstream filter — that filter is non-existent in ffmpeg and was the reason the old in-Space worker silently shipped raw bytes (see `prefetch.md` "what's gone"). The browser uses the frame count for `<audio>.duration`.

**`Xing` vs `Info` is load-bearing for CBR seeks.** ffmpeg always labels the tag **`Xing`** (the VBR marker) on `-c copy` when it sees *any* bitrate variation — including a single stray frame in otherwise-CBR audio (common: one 32 kbps frame at the head/tail). A `Xing` marker on a **CBR** file is poison for the browser-seek path that every CBR chapter uses: Chromium treats it as VBR and resolves `currentTime -> byte` through the coarse 100-entry TOC instead of exact linear math. Measured in headless Chromium against a real bucket file: **every seek stalls ~1 s then stutters in and lands up to ~0.8 s off**; linear play from the start is unaffected (no TOC used), so it only bites the per-segment click-to-seek path. This was the "segment audio merges / boundaries off / plays a different thing on seek" report on `abdur_rashid_sufi_qdc`.

The fix (`_force_info_if_cbr`): when a linear byte↔time map is accurate within `_CBR_LINEAR_SEEK_TOLERANCE_S` (0.2 s — separates stray-frame-CBR ≤0.16 s from genuine VBR ≥0.5 s), rewrite the 4-byte magic `Xing`→`Info` in place. Lossless: frame count, TOC and every audio frame are preserved; only the marker changes, flipping Chromium back to exact linear CBR seeking. Genuine VBR keeps its `Xing` TOC and routes through the server-side clip endpoint. Backfill existing bucket audio with `.local/extraction/backfill_xing_to_info.py`.

**Failure mode:** if ffmpeg returns non-zero / output is empty, `_ensure_xing` returns `False`; `_process_one` falls back to copying the source as-is. That chapter then has the legacy mis-seek issue — playback works, seek drifts. There is **no Space-side remux to retrigger** — re-run Katana extraction for the chapter. (No `ffmpeg_remuxed` audit field exists post-worker-removal.)

**Detect at runtime:**

```bash
ffmpeg -i reciters/<slug>/audio/<chapter>.mp3 2>&1 | grep -iE 'xing|vbri|info'
```

`Info` ⇒ CBR, linear seek (best). `Xing`/`VBRI` ⇒ VBR seek path (TOC). A `Xing` tag on a **CBR** chapter is the bug above — it should be `Info`; run `backfill_xing_to_info.py`. Absence with `bitrate_mode == "vbr"` in the sidecar ⇒ remux failed.

**Duration oracle for no-Xing VBR is the frame-walk, not ffprobe `format=duration`.** ffprobe's `format=duration` back-estimates from nominal bitrate ÷ size on a no-Xing VBR file → wildly wrong (the phantom-tail failure mode). Use the frame-walk (`mp3_probe` full path / `qua_shared.mp3_frames.build_frame_index`), which counts real frames. Evidence: mahmoud-ali-al-banna-mujawad ch27 — frame-walk 134,535 frames (matches ffprobe `-count_frames` authoritative 134,534 within one frame) vs ffprobe `format=duration` reporting 3523 s.

## Segment-clip route

`routes/audio/clip.py` — `seg_segment_clip(reciter)`, `segment_clip_bp`. Endpoint `/api/seg/segment-clip/<reciter>?url=…&start_ms=…&end_ms=…`. Streams an ffmpeg-extracted clip:

```
ffmpeg -hide_banner -loglevel error \
       -ss <start_sec> -i <source> -t <dur_sec> \
       -vn -c:a libmp3lame -b:a 96k -ac 1 -f mp3 -
```

**`-vn` is load-bearing.** The stripped static ffmpeg has no png encoder; an mp3-mux of a source with embedded APIC cover-art fails to "200 OK / 0 bytes" without it. Any new ffmpeg-mux path must replicate this.

Key properties:

| Property | Value | Why |
|---|---|---|
| Output bitrate | 96 kbps mono | matches audio_proxy tradeoff — speech-friendly, ~10 KB/s wire |
| Chunk size | 64 KB (`STREAM_CHUNK_BYTES`) | tight TTFB, amortised syscall overhead |
| Source preference | local file (mount/bytes) if present, else upstream URL | drops ffmpeg seek work from ~0.7 s (HTTP fetch) to ~0.15 s (local read) |
| URL allowlist | `_is_known_chapter_url` = `audio_meta.chapter_for_url(reciter, url) is not None` (**sidecar-keyed, not `detailed.json`** — the per-entry `audio` field is `""` post-migration-#5) | rejects open-proxy abuse |
| Scheme allowlist | `http` / `https` only | parsed via `urlparse` |
| CORS | `Access-Control-Allow-Origin: *` | required by `MediaElementAudioSourceNode` for the kill-switch to silence |
| Cache-Control | `public, max-age=31_536_000, immutable` | clip URL is deterministic on `(url, start_ms, end_ms)` so browser HTTP cache absorbs repeat plays |
| Process cleanup | timeout `FFMPEG_FULL_TIMEOUT = 300 s`, kill on timeout | logs `cmd` on timeout for forensics |

**ffmpeg can fetch HTTPS in the deployed image** (since the Dockerfile flipped to `--enable-openssl` + `http,https,tcp,tls`). The upstream-URL fallback path now works in deployed mode; the local-cache short-circuit is purely a latency optimization (~0.7 s → ~0.15 s for ffmpeg seek/decode).

## AudioPort VBR mode

`lib/playback/audio-port.ts` (~694 lines). The port owns the CBR-vs-VBR transport decision per call to `loadCovering(startMs, endMs, pad?)`.

VBR branch (`audio-port.ts:307-333`):

```ts
const clipStart = Math.max(0, startMs);
const clipEnd = needEnd;             // pad applied to end only
desiredUrl = buildClipUrl(reciter, audioUrl, clipStart, clipEnd);
desiredWin = { startMs: clipStart, endMs: clipEnd, offsetMs: clipStart, src: desiredUrl, isClip: true };
```

`offsetMs = clipStart` is the recovery: callers see file-absolute ms, port writes `(fileMs - offsetMs) / 1000` into `el.currentTime`. The clip plays from byte 0, no in-clip seek needed.

**Reuse rule for VBR clips** (`_canReuseWindow`, `audio-port.ts:426-440`; the strict-equality clip line is `:437`):

```ts
return current.startMs === desired.startMs && current.endMs >= needEnd;
```

Reusing a clip whose start is **earlier** than the requested start would force `currentTime` to seek inside the streamed clip — and a seek into a not-yet-buffered streamed response can stall or be ignored. This is exactly the failure mode the abstraction is supposed to hide. Strict equality on `startMs` is intentional. CBR reuse is more permissive: `current.startMs <= needStart && current.endMs >= needEnd`.

**Padding semantics:** `pad` extends `endMs` only in VBR mode. Padding the start would shift the clip start, breaking the reuse rule and forcing a re-fetch every call.

**Pending-promise reuse** (`audio-port.ts:342-349`): a follow-up `loadCovering` arriving during a swap that's already covered by the in-flight target window returns the same `pendingPromise` with `swapped: true`, so the caller awaits canplay instead of seeking into a still-loading element.

**Sync `_window` write** (inside `_swapTo`, `audio-port.ts:591-630`; `this._window = win` at `:605`): `_window` is set the moment the swap *starts*, not when canplay fires. A follow-up call during the swap sees the new window's `offsetMs` and either reuses (covers) or aborts and starts a fresh swap (doesn't cover) — instead of fast-pathing against stale data and writing wrong `currentTime` into a still-loading element. The audible regression this prevents: in VBR Adjust mode, an immediate split-left/right click after enter would seek and play before the wider clip's canplay had updated `_window`, landing the playhead in the wrong file-absolute position.

## Frontend VBR routing

`audio_meta.vbr_chapters_for_reciter(reciter)` is shipped to the frontend as a sorted list of VBR chapter numbers. `tabs/segments/utils/playback/range-spec.ts::vbrClipForChapter` consults this list to decide CBR vs VBR per segment — works for cross-chapter accordion rows (sibling jumps that span chapters with different encoding).

Non-integer keys (by_ayah `"<s>:<a>"`) are skipped — only by_surah deliveries expose VBR today.

## VBR-only bug shapes

| Symptom | Root | First probe |
|---|---|---|
| Seek stalls ~1s + lands <1s off + stutters, on a **CBR** chapter; linear play is fine | `Xing` (VBR) marker on CBR audio → browser uses coarse TOC instead of linear seek | tag must be `Info` not `Xing`: `ffmpeg -i <ch>.mp3 2>&1 \| grep -iE 'xing\|info'`. Fix: `backfill_xing_to_info.py` |
| Seek lands seconds off, only on Adjust mode | `_window` not yet updated when split-click fires; pending-promise reuse logic skipped | check `audio-port.ts:342-349` is not bypassed by a custom load path |
| Seek lands seconds off, all modes | Xing remux failed → raw VBR shipped | `ffmpeg -i reciters/<slug>/audio/<ch>.mp3` look for Xing tag |
| Seek drift growing with position (~0.15 %, 5 s an hour in) on a **CDN-direct** chapter; last seconds unreachable | untagged CBR whose encoder never sets the padding bit (quranicaudio `abdul_muhsin_alqasim` 004/005: every 96 kbps frame 313 B, nominal 313.47) — Chrome seeks untagged files at the nominal rate | `mp3-header.ts::runsAtNominalRate` walks the sniffed frames and keeps such files (and untagged VBR) on the proxy; check with `ffprobe -show_entries packet=size` |
| Seek drift growing with position, **every chapter** of a YouTube/Drive intake | off-cluster local-staged source bypassed the canonical re-encode — `download.sh`'s ffmpeg `--postprocessor-args` silently no-op'd, so 48k/stereo/VBR shipped verbatim (the yt-dlp **URL** path re-encodes to forced CBR; the **local-file** staging path did not) | `ffprobe reciters/<slug>/audio/<ch>.mp3` shows non-44.1k / stereo / VBR. Fix: canonicalize in `audio_io._download_and_load_one`'s local branch (qua-aligner-offline), then `persist-audio` re-fetch + re-upload `--force` |
| Clip URL works in dev but 500s in deployed | upstream URL fetch failed inside ffmpeg — usually network egress block, not the binary | check Space outbound rules; confirm `curl -I <url>` works from inside the container |
| Reusing same clip plays wrong content | reuse-rule violation — `startMs` not equal | log `current.startMs`, `desired.startMs` from `_canReuseWindow` |
| Cross-chapter accordion plays wrong transport | frontend `vbr_chapters_for_reciter` stale (sidecar updated, FE didn't refetch) | check the per-reciter VBR map exposed to the frontend |
| Audible glitch at clip boundary in advance mode | `endMs` clip end didn't have post-roll pad — boundary flush has no audio to drain | bump `defaultPadMs` on the AudioPort instance |
| Clip 403s in browser | `_is_known_chapter_url` rejected — URL not in `detailed.json` for that reciter | check `entry.audio` in `detailed.json` matches exactly |
| Clip plays but stops short by ~0.5s | ffmpeg `-t` truncation at frame boundary; expected for VBR | acceptable, document if user reports |

## Dataset packaging — slicing chapter audio

The HF dataset (`scripts/release/build_reciter.py`) slices per-ayah audio out of chapter files. When it sources from the **bucket** chapter (provenance: same bytes MFA aligned against) instead of re-downloading from the CDN, two VBR-specific gotchas apply — both measured, not theoretical:

**Per-slice Xing depends on the output being seekable, not on `-f mp3`.** A mid-chapter `-c copy` slice written to a `.mp3` **file** gets a fresh per-slice Xing TOC automatically (the `.mp3` extension already selects the mp3 muxer, and the muxer backfills the frame count because the file is seekable). The `-f mp3` flag only matters when the output path has no `.mp3` extension. The real failure case is **piping to stdout** (`-f mp3 -`): ffmpeg can't seek back to backfill the count, Xing offset comes out `-1`, and the slice reintroduces the canonical VBR seek-drift bug. **Rule for the packaging writer: slice VBR chapters to seekable files, never through a pipe.** (CBR slices need no Xing — linearly seekable.)

**`-c copy` is sample-inaccurate; re-encode VBR.** Frame-boundary snap is ~26.12 ms at 44.1 kHz (24.00 ms at 48 k), and the bit-reservoir + 529-sample decoder priming put a click at each copy-slice head. The current pydub path cuts sample-accurately. So: copy CBR ≥128k chapters (accept ~26 ms snap, or re-offset stored clip-relative timestamps), but **re-encode VBR chapters** (`-c:a libmp3lame` at ~source bitrate) — sample-accurate, clean gapless info, near-source quality. FLAC isn't worth the size for speech.

**Audio durability.** Chapter audio lives **only** at `reciters/<slug>/audio/<ch>.mp3` (`storage_paths.py`) — lifecycle state is a DB attribute, not a folder, and nothing copies audio on publish. Audio + peaks now **persist indefinitely** (the wip-audio sweeper was removed), so packaging that sources from the bucket can run at any time without a TTL race. When the bucket genuinely lacks a chapter, the script falls back to CDN re-download.

## When to add a new mitigation

VBR mitigations should live in either Xing-injection (extraction-time, on Katana) or segment-clip (request-time). Don't add a third path — adding a "fix in the AudioPort" is debt; adding a "let the frontend probe encoding mode" duplicates the sidecar.

YouTube / yt-dlp sources sidestep this entirely: extraction re-encodes them to **forced CBR** (192k / 44.1k / mono — see `extraction-intake.md`), so they never carry the VBR-without-Xing drift the mitigations above exist for.

If a new bug class shows up, prefer:

1. **Offline fix at extraction time** (modify `audio_persist.py::_ensure_xing` flags, add a fallback codec path; re-run extraction). No in-Space remux path exists to patch.
2. **Backend fix at request time** (segment-clip parameters, source preference).
3. **AudioPort transport tweak** only when the bug is genuinely browser-only (e.g. specific browser autoplay policy interaction).

Don't try to fix VBR bugs in `audio-range.ts` or per-component playback code — the abstraction exists exactly to keep that contamination out.
