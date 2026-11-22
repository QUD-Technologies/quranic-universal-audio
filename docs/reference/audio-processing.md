# Audio processing and source defects

The one place for **how audio moves through the system** and **every audio defect we have hit**,
from the publisher's file to the listener's speaker: intake, acquire/encode, split, peaks and
levels, the aligner, playback, releases and upstream drift. Subsystem references stay the
contract for their own mechanics (linked per section); this doc is the cross-cutting map and
the defect catalogue.

**Read it when:** a recitation "can't be matched", a chapter aligns only in part, audio sounds
or seeks wrong, waveforms don't line up, an acquire/split job fails, or you are adding a new
source host or audio step. Start at [Triage](#triage-symptom--first-check).

Status legend: **fixed** (code handles it) · **guarded** (detected and reported, run continues)
· **runbook** (manual procedure) · **open** (known, not handled).

---

## 1. The audio path

```
publisher file (mp3quran / tarteel / quranicaudio / way2quran / tvquran / archive.org / Drive / YouTube)
  │  intake plan (playlist / Drive folder / links) → audio manifest catalog/audio_manifest/<slug>.json
  ▼
acquire   HF CPU job qua_jobs/acquire_audio.py ── fetch → phase scan → canonical encode → peaks + levels
  │        → reciters/<slug>/audio/<ch|slot>.mp3, peaks/<ch>.json.gz, levels/<ch>.json.gz
  ▼
align     aligner Space (qua apps/aligner) reads hf://buckets/…/audio/<n>.mp3 by reference
  │        decode → mono 16 kHz → 80 Hz high-pass → boundary-head segmentation → ASR → anchor → DP match
  ▼
split     cut combined/playlist files per surah (qua_jobs/split_audio.py) + coverage guards
  ▼
sidecars / timing   aligner neural timing per chapter (audio_frames/<ch>.bin frame index) → shards
  ▼
playback  play-url.ts: CDN direct when safe, else Flask proxy (bucket copy) ── Segments clip route for VBR
  ▼
releases  GH/HF cut: verse clips sliced by frame index; drifted chapters dropped
```

### Two writers of bucket audio — know which one made the file

| Writer | Deliveries | What the bucket mp3 is |
|---|---|---|
| **Native pipeline** (`qua_jobs`, current) | everything aligned via the Inspector Align button | a **re-encode** of every source: libmp3lame 192 kbps CBR, 44.1 kHz, source channel count (or the delivery `channels`), −1.5 dBFS limiter, phase repair, cover art stripped |
| **Katana offline extraction** (retired, code not in repo) | older released reciters | the publisher's bytes **verbatim** (`-c copy`), ID3 stripped, `Xing`→`Info` rewritten on CBR (`_force_info_if_cbr`) |

Docs written in the Katana era (`dataset-and-releases.md` "preserve, don't normalize", parts of the
`inspector-audio` skill's `vbr.md`/`prefetch.md`) describe the second row. For native deliveries the
manifest still describes the **source** (`size_bytes` is the upstream drift baseline; `bitrate_*`
stays the source's), not the 192k bucket copy.

### Canonical encode (native) — `qua_jobs/audio_io.encode`

`-vn` · `-af [phase filter,] alimiter` · `-c:a libmp3lame -b:a 192k -ar 44100 -ac <channels> -f mp3`.
`CEILING_DBFS = -1.5` → `LIMITER = alimiter=limit=0.8414:attack=5:release=80:level=0:latency=1`
(delay-compensated: the timeline does not move). Input via `_input()` (adds `-f mp3` when ffmpeg
sniffs no audio). Also `encode_pieces` (a surah in parts: `aformat` + `concat` + limiter) and
`download_audio._LIMITER` (consumer helper, kept equal by a test).

### Aligner decode paths (qua `apps/aligner`) — they do not see identical samples

| Path | Used by | Decode | High-pass 80 Hz |
|---|---|---|---|
| a | align, batch, segmentation, **sidecars** | ffmpeg `f32le -ac 1 -ar 16000` (librosa `soxr_lq` fallback), **no timeout** | yes — **except sidecars** |
| b | chapter timing, Katana re-time | ffmpeg `s16le -ac 1`, `DECODE_TIMEOUT_S = 600` | no |
| c | `timestamps_direct`, `ref_edit`, session reload | librosa (`soxr_lq`; reload uses librosa default) | no |
| d | API numpy tuple | channel mean + librosa resample | yes |

Every path downmixes by **plain averaging** — no channel selection, no correlation check. That is
why polarity defects (§3.1) must be repaired before the aligner ever sees the file. No loudness
normalisation (the recognition encoder normalises per item to zero mean / unit variance; dead-air
detection is absolute dBFS). No clipping detection.

---

## 2. Intake and source fetch

Mechanics: [`align-pipeline.md`](align-pipeline.md) (intake plan, acquire), [`catalog.md`](catalog.md)
(manifests, slug, cross-channel fill), the `intake-prefetch` skill (local acquire).

| Defect | Symptom | Cause | Detection / fix | Status |
|---|---|---|---|---|
| **YouTube bot check from datacenter IPs** | acquire "Sign in to confirm you're not a bot"; plan "could not list…" | HF Jobs (AWS AS14618) and Katana refused on every player client; cookies rotate within hours; block is volume-triggered (a 1-file probe passes) | `_BOT_CHECK` match → `BotCheckError`; remaining YouTube sources fail fast; `YTDLP_WORKERS = 2`, `--sleep-requests 1`; secrets `INSPECTOR_YTDLP_COOKIES` / `INSPECTOR_YTDLP_PROXY` (`622b65ec`) | guarded; durable fix = residential proxy (wired, unset); runbook = intake-prefetch (local acquire, one slug at a time, `ACQUIRE_WORKERS` 1–2, format `-f 140` not opus 251, `--js-runtimes node`) |
| Cookie secret pasted with spaces | cookies silently ignored | Netscape format needs tabs | `normalize_cookies` rebuilds 7 tab fields | fixed |
| Playlist listing dropped mid-response | `SSL: UNEXPECTED_EOF_WHILE_READING` | flaky YouTube | `_LIST_ATTEMPTS = 4`, linear back-off (`a450ea3c`) | fixed |
| Truncated playlist listing | fewer entries than reported | old yt-dlp / region lock | listing shorter than the reported count is refused | guarded |
| Drive answers HTML | "Drive answered with a web page…" | not shared publicly / quota | `drive.usercontent.google.com …&confirm=t`; `refuse_html` | guarded; Retry later |
| Drive folder capped at 50 files | missing files | page scrape limit | Drive v3 `files.list` with scraped key (`INSPECTOR_GOOGLE_API_KEY` first) | fixed |
| archive.org / Drive 5xx | one HTTP 500 failed a 114-file acquire; on Katana a different file lost each run | host under load | `HTTP_ATTEMPTS = 4`, `HTTP_RETRY_SLEEP_S = 5`×n, 4xx fails at once (`6105d018`) | fixed |
| Stale archive.org node hosts | `ia8xxxx.us.archive.org/...` times out | node URLs rot | `canonical_archive_url` → `archive.org/download/<item>/<file>`; `audio_url_audit.py`; `backfill_audio_manifest_meta --rewrite-urls` (re-stamp checksum) | runbook |
| Large ID3v2 cover art | head probe finds no frame (`no_frame_in_head`); archive tags ~1.5 MB, tvquran ~285 KB | tag before first frame | `mp3_probe._id3_size` range-fetches past it | fixed |
| Cloudflare ignores Range on cold miss | 200 instead of 206 | CDN behaviour | `_fetch` re-slices the body (load-bearing branch) | guarded |
| MPEG-2/2.5 files | probe reads 0 frames | lower sample rates on archive/tvquran | multi-version frame parsers | fixed |
| Format varies **per chapter** | wrong delivery-level assumptions | e.g. nabil-hatim 77×44.1k + 37×48k; ~18% of deliveries VBR | probe per chapter, never per delivery | runbook |
| Truncated download passes silently | coverage "clean" but verses missing (ahmad_naseem 008: 210 s vs 1876 s) | yt-dlp `--no-overwrites/--ignore-errors` keep partials | compare probed duration vs yt-dlp `%(duration)s`, refetch < 90%; split `MIN_ALIGNED_COVERAGE` now flags it (§4) | guarded |
| yt-dlp resample arg ignored | 48 kHz kept, probed `mode=mixed` | `--postprocessor-args -ar` unreliable | separate ffmpeg pass (native encode always resamples) | fixed (native) |
| Local paths leaked into manifests | scratch paths on the bucket (#285) | offline intake | schema rejects absolute paths / `file:`; `validate_bucket.py` flags legacy (`f3b65eaa`) | fixed |
| mp3quran host migration | legacy `serverN` URLs | moved to `cdn.mp3quran.net/audio/<reciter>/rN/<ch>.mp3` (rN = moshaf index, not a version); dropped files 302 to `/audio/legacy/` | migration 0033 + `canonicalize_mp3quran_urls.py` (shared keep-alive pool fixed WinError 10048) (`ee9e858e`, #312) | fixed |
| Unregistered upstream sets | missing reciters | mp3quran adds sets | `mp3quran_catalog_diff.py` (#341) | monitor |
| Recording year changes slug | mint reads wrong path ("0 surahs") | year is in the slug | set `--recording-year` at submit | runbook |
| Arabic ID3 tags | text decode errors | tags not cp1252 | capture ffprobe output as bytes | fixed |

---

## 3. Acquire and encode — source-file defects

### 3.1 Polarity (antiphase) — the "audible but unalignable" class

**Symptom.** Sounds normal in a stereo player, but the aligner hears nothing: "Could not anchor to
any chapter — no n-gram run cleared the evidence floor", `no_speech_detected`, or a chapter that
matches only up to a point. **Cause.** One channel is polarity-inverted (L = −R). Every VAD/ASR
pass folds to mono (§1 decode table), which cancels the voice to reverb residue. **Detection.**
`audio_io.phase_layout` reads the **whole** file with `aphasemeter`, takes the per-second median of
non-silent frames (`PHASE_SILENT = 0.1`), counts seconds beyond `±PHASE_DECISIVE = 0.5`:

| Layout | Rule | Repair | `acquire.json` |
|---|---|---|---|
| `in_phase` | majority in phase (incl. true stereo) | none | — |
| `inverted` | majority inverted | `pan=stereo\|c0=c0\|c1=-1*c1` | `phase_inverted: true` |
| `mixed` | ≥ `PHASE_MIN_RUN_S = 10` s of **both** | left copied to both: `pan=stereo\|c0=c0\|c1=c0` (lossless for mono-in-stereo masters, \|L\| = \|R\|) | `phase_mixed: true` |

Cases: `maher_al_muaiqly_muallim_mp3quran` (r2) — 35/38 chapters inverted, L/R corr −0.94, 0/38
alignable (PR #314). `hassan_saleh_mp3quran` 023 — in phase to 3:08 then L = −R (an edited
master); the original 120 s probe missed it, 92 ayahs unmatched; realigned 118/118 (PR #349).
True-stereo masters (`mohammed_al_muhaisni_mp3quran` ch24/26/42/71/73/75/76: corr +0.35…+0.48,
mono fold −1 to −2 dB) read `in_phase` and are untouched.

**Manual check:** `ffmpeg -i f.mp3 -af aphasemeter=video=0,ametadata=mode=print:key=lavfi.aphasemeter.phase:file=- -f null -`
or numpy L/R `corrcoef` per 30 s window. **Open gaps:** see §9 (delivery `channels = 1`, direct
CDN playback, release download helper, legacy Katana deliveries never scanned).

### 3.2 Other source-file defects

| Defect | Symptom | Cause | Detection / fix | Status · example |
|---|---|---|---|---|
| **Hot master clips** | peaks > 0 dBFS after MP3 reconstruction; offline `-ac 1` mono fold clipped vowels | hot source + lossy encode | limiter `CEILING_DBFS = -1.5` (#347); keep source channel count, never force mono | fixed for new encodes; old bucket audio not re-encoded. Stereo re-encode of 9 Katana reciters 2026-06 (`bucket-reencode` runbook) |
| ffmpeg 8 sniffs mp3 as VVC video | probe/phase/encode fail | ffmpeg demuxer sniffing | `_format_hint` adds `-f mp3` when no audio stream (#336) | fixed · mp3quran `abdulmohsen-obaikan/r1/036.mp3` |
| `.bin` → bintext demuxer | exit 234 "no stream" on big-ID3 files | extension picked the demuxer | download to extensionless `src` (#313) | fixed · `hady_toure_qdc` (10 ch) |
| Cover art breaks mux | 0-byte output | APIC stream → png encoder | `-vn` everywhere | fixed |
| Digital-silence chapter | chapter unalignable | upstream defect | default: drop delivery; with user OK fill from **the same recording** on another channel | runbook · `salman_al_sadeiq_mp3quran` ch42 → way2quran ([`catalog.md`](catalog.md) "Cross-channel chapter fill") |
| Upstream truncation | file ends mid-word, loud to the last frame; header length matches frame count (cut at mastering) | publisher | count frames (`ffprobe -count_packets`) — never trust header/manifest duration; check last 500 ms level | open per case · way2quran salman ch2–6 (5–27% of audio, delivery discarded); `neamah_al_hassan_mp3quran` ch7 ends inside 7:206 (TS fails on that one segment, left to reviewer) |
| Intake cut before recitation ended (YouTube/Drive) | last word clipped | cut point too early | `.local/cut_audit/` (peaks + last segment, no download); `.local/tail_extend/` re-encodes `[start, word_end+500 ms]` by cross-correlation | runbook · minshawi_1967 ch30, burhaji ×8, buaijan 64/106, ahmad_naseem 35/69/82, saber 114 |
| Wrong surah in the file | another surah's audio | upstream mislabel / duplicate | split `mismatched` / `repeats` / `gaps` → `coverage_report.json`; fix by sourcing the right recording | guarded · `adel_al_karbalaei_archive_v2` 046 = copy of 047 |
| Two surahs in one file / spill | neighbour's verses inside a chapter; release times against wrong mp3 (#284) | upstream mis-cut | split resolver for combined sources; manual head/tail trim (measure offset by cross-correlation — `-ss -c copy` lands up to ~68 ms off) | runbook · Sufi Shu'bah 89 (88:16–26 head), Sobhi 54 (+ all of 44), Hudhaif way2quran 082 (81+82) |
| Phantom tail | manifest/format duration ≫ audible; deep seeks 502 | VBR without Xing, duration estimated from bitrate; trailing dead bytes | `audio_metadata_sweep.py --peaks --drift-pct 8`; `backfill_audio_manifest_meta --fix-drift`; clip route returns 502 | guarded · `maher_al_muaiqly_mp3quran` ch76 (449 s vs ~300 s) — data curation still TODO |
| Splice voices in openers | "own basmala elsewhere = own voice" false | reciters splice one foreign basmala into every chapter | aligner `basmala_voice` z-score (Hafs/Shu'ba Fatiha) + opener backfill per source | guarded ([`specials.md`](specials.md)) |
| Teacher + students (muallim) repeats | students' isti'adha/basmala anchors in Fatiha; chapters lose verses | every chunk said twice | aligner consumes repeated formulas (qua `4f749b12`); pairing audit = identical-ref runs of exactly 2 | fixed; open: sister-chapter anchors (26↔28, 45↔46, 59↔61), near-identical consecutive verses |
| Fast recitation, no silence | VAD segments up to 763 s, ASR OOM | YouTube noise floor ~−24 dB defeats `silencedetect` | energy-minimum force split; `atempo=0.5` (pitch-preserving, never resample) | fixed (offline) |
| Reverb / noise field recordings | mega-regions, zero matches | RT60 18.6 s → 1 s pause dips 3 dB | aligner 80 Hz high-pass helps rumble/hum; denoise/WPE harmful; use Large | open (labs) |
| Digital silence at stitch joins | empty regions | head trained on room tone | `DEAD_AIR_DB = -50`, `DEAD_AIR_SHARE = 0.9` drop | fixed (aligner) |

---

## 4. Split, cut and coverage

Mechanics: [`align-pipeline.md`](align-pipeline.md) §split. Outcome → `split_outcome.json` →
`coverage_report.json` (`missing`, `unresolved_files`, `clean`).

| Guard | Rule (constant) | Example |
|---|---|---|
| Mislabelled single file | dominant other surah ≥ `MISMATCH_SHARE = 0.6` → dropped before the cut | `mohammed_burhaji_yt` |
| Excerpt fragment | unplanned surah < `MIN_SURAH_COVERAGE = 0.5` → not adopted | Afasy Juz ʿAmma CD intro montage |
| Suspect cut | ≥ `SUSPECT_MIN_MS = 3000` unmatched next to a gap | al-Ikhlāṣ + al-Falaq |
| **Low coverage** | kept chapter covering < `MIN_ALIGNED_COVERAGE = 0.8` of its ayahs → `unresolved_files`, `clean = false` (run continues) | `hassan_saleh_mp3quran` 023 before repair (26/118) — PR #349 |
| Gap / repeat / empty source | reported | `adel_al_karbalaei_archive_v2` |
| Cut length | `LENGTH_TOLERANCE_MS = 1500`; every chapter re-cut each run | — |
| Mount reads during job writes | 2.7 s "chapters" with exit 0 → all job I/O over the Hub HTTP API (`bucket_io`, size-checked, `ATTEMPTS = 4`) | 2026-09-26 |
| Stale aligner listing after split | `audio_fetch_failed` → aligner drops its `HfFileSystem` cache before every fetch (qua `a5440c0b`) | — |

Cut windows: `TRIM_PAD_MS = 300`, collisions cut at the silence midpoint; a surah in parts is
stitched when ≥ `PIECE_MIN_NEW_SHARE = 0.8` of a piece is new. Slots: `SLOT_BASE = 201`.

---

## 5. Peaks, levels and manifest metadata

Mechanics: [`audio-metadata-pipeline.md`](audio-metadata-pipeline.md), the `inspector-audio` skill
(`peaks.md`, `vbr.md`, `probes.md`).

| Defect | Symptom | Fix | Status |
|---|---|---|---|
| CBR/VBR mislabel | seek stalls; a stray frame flipped CBR→VBR, late VBR read CBR | one whole-file metric: `max_linear_seek_error_ms ≤ CBR_LINEAR_SEEK_TOLERANCE_MS = 200` → CBR; full-file probe (`24a339db`) | fixed |
| Null `bitrate_mode` treated as CBR | VBR routed as CBR | `audio_metadata_sweep.py` / `backfill_audio_manifest_meta.py` | runbook · `ayman_swed_muallim_tvquran` |
| No-Xing VBR duration | `format=duration` wildly wrong | frame walk is the oracle | runbook |
| Manifest without duration | scrubber 0:00 | fallback to peaks header `duration_ms` (`dbbca3e6`) | fixed |
| `mixed` rows with nominal kbps | dashboards 500 | migration 0015 | fixed |
| Slim peaks misaligned | waveform silence off audio by ≤ 100 ms | absolute-time pixel mapping (`4f67cc80`, `3af65dde`) | fixed |
| Peaks past EOF | blank canvas on last segment | requests capped at EOF (`9078f431`) | fixed |
| Peaks route deadlock | hang on cache misses | non-reentrant lock fix (`9e107cbb`) | fixed |
| Levels bake EIO on mount | whole run failed | `LEVELS_ATTEMPTS = 3`, then `levels_missing` (joins all asked) (#282) | guarded |
| Encoder delay | false "divergence" bucket vs CDN | benign ~12 ms; compare with full-res FFT xcorr | n/a |

Formats: peaks `PEAKS_BUCKETS_PER_SEC = 30` → slim `10` bps int8, gzip `mtime=0`
(`PEAKS_SCHEMA_VERSION = 3`); levels 20 ms RMS dB int8 at 16 kHz (`RMS_FLOOR = 1e-5`).
Pause verdicts read levels: `MIN_SILENCE_MS = 40`, `MIN_DIP_DB = 10`, `MIN_FLOOR_MS = 160`
(non-Hafs `120`), `VERSE_END_FLOOR_MS = 200` — see [`align-pipeline.md`](align-pipeline.md).

---

## 6. Aligner-level handling (qua `apps/aligner`, `packages/sdk`, `packages/timing-batch`)

| Item | Behaviour | Constants |
|---|---|---|
| Bucket fetch | ref must match `EXTRACTION_AUDIO_REF_PATTERN`; `X-Extraction-Secret`; any error → 502 `audio_fetch_failed`, **no retry** | — |
| 80 Hz high-pass | 4th-order zero-phase Butterworth after decode; hall F1 0.676→0.747, old desk 0.189→0.609 | `ANALYSIS_HIGHPASS_HZ = 80` (0 disables) |
| Segmentation | boundary head `furqan-v2-base/large` (no VAD); slow pace → min run 300 ms; fast pace → lower bar | `SLOW_PACE_MS = 2000`, `FAST_PACE_MS = 350`, `BRIDGE_FRAMES = 20` |
| Edge artifacts | edge regions < 0.3 s dropped | `EDGE_MIN_SPEECH_S = 0.3` |
| Anchor | n-gram ladder; refuse below evidence floor → `ANCHOR_NOT_FOUND` | `min_run_hits = 4`, `min_run_margin = 1.5`, window ≤ 20 segs |
| Short clip refused on margin | last-resort unmargined anchor on Large | — |
| Silent Large retry | Base unanchored / no matches / mean conf < 0.8 → Large; both fail → 422 `alignment_failed` | `SILENT_RETRY_MIN_CONFIDENCE = 0.8` |
| Non-Quran lead-in | skip ≤ 3 segments within 30 s before the opener | `opener_lead_segments`, `opener_lead_seconds` |
| False cuts | cuts on pauses ≤ 0.1 s re-timed and merged (measured unpadded) | `MAX_PAUSE_S = 0.1`, `MERGE_MARGIN = -10` |
| Frame index | `audio_frames/<ch>.bin` refused on mid-stream rate change, unpinnable gapless delay, < 26 frames, or any of 8 check spans ≠ whole decode; stored index used only when built from the same content hash | — |
| GPU lease | 120 s cap → Base ~8 h audio (5.7 h with review checks), Large ~4.8 h; quota → CPU | `ZEROGPU_MAX_DURATION = 120` |

Not handled at the aligner (by design, repaired upstream or open): polarity, clipping, loudness
normalisation, size/duration limits, fetch retry, decode timeout on path (a), truncated-file
warning (ffmpeg exits 0 on decode errors → shorter audio aligned silently).

---

## 7. Playback, proxy and the browser

Mechanics: [`frontend.md`](frontend.md), the `inspector-audio` skill (`backend.md`, `frontend.md`,
`bugs.md`).

`play-url.ts` plays the **CDN directly** only when the host answers 206 to a CORS Range request,
the file seeks natively (`mp3-header.isNativelySeekable`) and its live size equals manifest
`size_bytes`; otherwise the **proxy** serves the bucket copy (`range_file.send_range_file`,
1 MB reads). Segments plays VBR through `/api/seg/segment-clip` (ffmpeg re-encode, sample-exact).

| Defect | Symptom | Fix | Status |
|---|---|---|---|
| **Xing TOC on a CDN file** | seeks ±3–12 s off on long chapters, `currentTime` lies (sufi 20:113 +12.45 s) | Xing files stay proxied (`d5d4adba`) | fixed |
| **iTunSMPB gapless sample count** | duration and seeks scaled (~1.3×); mp3quran stamped one template on every Surah 24 | gapless tag → proxy (`4d7c737e`) | fixed |
| Untagged CBR without padding bit | seeks 5 s short after an hour | `runsAtNominalRate` (`0891a797`) | fixed |
| CDN swapped a chapter | one chapter out of sync (tarteel husary mujawwad 062) | size check → proxy (`f1ebbb5f`) | fixed |
| Xing on CBR (Katana copies) | ~1 s seek stall, ≤ 0.8 s off | `Xing`→`Info` remux | fixed (legacy) |
| VBR without Xing TOC | seeks seconds off | segment-clip route | guarded |
| Clip route 200 with silence | header-only clip played as silence | first chunk < `MIN_VALID_CLIP_BYTES = 1500` → 502 (`fcc03843`) | fixed |
| Clip 0 bytes | static ffmpeg lacked lame / cover art | lame built from source; `-vn` | fixed |
| Redirect silenced Web Audio | silent `<audio crossorigin>` | stream CDN same-origin with ACAO (`e272407e`) | fixed |
| Cross-origin tainted element | silent with healthy `readyState` | `crossorigin=anonymous` on kill-switch ports (#248/#249) | fixed |
| Upstream errors cached a year | stuck broken audio | only 200/206 get immutable cache; errors `no-store` (`fcc03843`) | fixed |
| Slow far clients | 11 s Range, starved JSON | direct CDN play; 1 MB proxy reads (#248) | fixed; probe one request at a time (6 parallel probes wedged the prod mount) |
| Region latency | ~200 ms TTFB from US origin | public dataset mirror proposed | open |
| Range end ignored in hidden tab | card played past end | `timeupdate` backstop | fixed |
| Audible tail after pause | OS sink buffer | 5 ms kill-switch gain ramp | fixed |
| Playhead ahead of audio | ~54 ms lead on Chromium/Win | latency compensation + buffering gate | open: latency cache inert while `outputLatency` reads 0 (`audio-graph.ts`) |
| LFS pointers in Space build | guide clips = 131-byte pointers | HF build does not smudge LFS | open |

Direct CDN playback serves the **original** bytes: no limiter, no phase repair (§9).

---

## 8. Releases, downloads and upstream drift

Mechanics: [`dataset-and-releases.md`](dataset-and-releases.md), the `upstream-drift` skill.

- **HF verse clips** — frame-index slicing (`mp3_frames.slice_frames`), start snapped to the frame
  at/before the clip (~26 ms); interior no-match audio excised by `slice_frames_multi`. Fixed:
  `-ss` overshoot clipping ~50 ms of onsets, burhaji slice past EOF, clip bounds missing
  word bleed (~10k verses, `94869c78`).
- **Release links** use `public_source_url` (never a bucket URL); untimed chapters get no URL.
- **`download_audio.py`** (consumer helper) re-encodes non-CDN sources with the limiter, default
  **mono**, `-c copy` trims — no phase repair.
- **Upstream drift** (`qua_shared/audio/upstream.py`): publishers re-upload under the same URL
  (mp3quran has no versioning; `ETag` = MD5, `Last-Modified` dates the swap). Verdicts
  `recording` / `reencode` / `tag_edit` / `resized` / `gone` (`DURATION_TOL_S = 2.0`,
  `TAG_EDIT_BYTES = 65536`). Align start **adopts** a replaced source for un-aligned deliveries
  (deletes stale audio/peaks/levels); the release cut **drops** drifted chapters
  (`dropped_upstream_chapters`); fleet audit `scripts/diagnostics/upstream_drift.py`. Unpadded
  CBR re-encodes misread as new recordings fixed in #345.
- **Released chapter realign** — no built-in tool: run the native pipeline under a shadow slug
  `<slug>_rl<chs>` and splice into the live files (`.local/realign/phase_a…e`, memory runbook);
  realigned chapters lose review edits.

---

## 9. Open gaps and risks

| Gap | Risk | Suggested fix |
|---|---|---|
| **Delivery `channels = 1` skips the phase scan** (`runner._acquire` → `CHANNELS`; `acquire_audio` scans only when channels == 2) — 564 prod deliveries are `channels = 1` | an antiphase stereo source is downmixed to near-silence | scan whenever the *source* is stereo; apply the phase filter before `-ac 1` |
| Legacy (Katana) deliveries never phase-scanned | undetected antiphase in released audio | fleet scan by streaming CDN URLs (≈2 s per 20-min file) |
| Direct CDN playback / `download_audio.py` skip phase repair + limiter | antiphase source near-silent on mono devices (phone speaker) | route `phase_*`-flagged chapters through the proxy; mirror the repair in the helper |
| Aligner bucket fetch has no retry; decode path (a) no timeout | transient 502s; a hung ffmpeg holds a worker | retry/backoff like `packages/datasets`; add a timeout |
| Truncated file aligns silently | shorter audio, missing verses | low-coverage guard catches large losses; frame-count vs header check at acquire |
| Native manifest `bitrate_*` describe the source, not the 192k copy | warmup byte offsets at wrong kbps when proxied | record bucket-copy fields separately |
| Dropped chapter leaves `levels/<ch>` (`_delete_dropped_audio`) | orphan file | delete with audio + peaks |
| Phantom-tail chapter data (maher ch76) | wrong duration | curate or drop |
| `ibrahim_al_akhdar_drive` ~30 flagged tails | possible clipped endings | unresolved |

---

## Triage: symptom → first check

| Symptom | First check |
|---|---|
| "Could not anchor" / `alignment_failed` on audio that sounds fine | L/R phase (§3.1) — whole file, not the opening |
| Chapter aligns to a point, then fragments | `coverage_report` low coverage; phase per 30 s window; file truncation (level of last 500 ms, frame count) |
| Every YouTube source fails | bot check (§2) → intake-prefetch skill |
| One chapter out of sync, segments look right | upstream drift skill; `play-url` size check |
| Seeks land seconds off | Xing / iTunSMPB / padding bit (§7); VBR without TOC |
| Waveform silence ≠ audio silence | slim-peaks mapping; peaks past EOF |
| TS fails on one segment ("audio is shorter than its sounds") | segment duration vs words — usually a source cut mid-recitation |
| Acquire exit 234 / "no stream" | demuxer sniffing (`-f mp3` hint, extensionless download) |
| Clipping / distortion | limiter present? (native only); source peak level |

## Tools

Diagnostics (`scripts/diagnostics/`): `audio_metadata_sweep.py` (null metadata, duration drift vs
peaks), `audio_url_audit.py` (dead/fixable URLs), `upstream_drift.py` (fleet drift),
`mp3quran_catalog_diff.py` (new/gone sets), `validate_bucket.py` (schema + local paths),
`audit_timestamp_pauses.py`. Backfills (`scripts/backfills/`): `backfill_audio_manifest_meta.py`
(`--fix-drift`, `--rewrite-urls`, `--remove-dead`), `canonicalize_mp3quran_urls.py`,
`backfill_peaks_slim.py` / `rollback_peaks_slim.py`, `patch_release_audio_sources.py`.
Skills: `inspector-audio` (deep audio subsystem), `intake-prefetch` (local acquire),
`upstream-drift`. Aligner: `dev-space-ops` scripts (`space_logs.py` — `[DECODE]`, `[LEASE]`,
`anchor=` lines), `REQUEST_LOG=1` keeps request audio in `QUD-Technologies/aligner-logs`.

## Known stale statements elsewhere

Code wins; fix these when touching the doc. `dataset-and-releases.md` "preserve, don't normalize"
and the `inspector-audio` skill's `vbr.md` / `prefetch.md` / `bugs.md` describe Katana bytes
(§1); `vbr.md` "YouTube forced mono" (native keeps source channels); the `upstream-drift` skill
says Align start refuses a `recording` change with 409 (it adopts it); `align-pipeline.md` says
split re-measures cut chapters (it re-cuts every run); `extraction-intake.md` slot `901+` (code
`SLOT_BASE = 201`); `catalog.md` §12–13 head-probe + mutagen and checksum format (code uses the
whole-file metric and `sha256("k=url;"…)[:16]`); `catalog.md` cross-channel fill omits deleting
`levels/<ch>`; qua aligner `CLAUDE.md` "402 on quota" (it falls back to CPU) and the
`audio_filters` "VAD" wording (the boundary head is the only segmenter).
