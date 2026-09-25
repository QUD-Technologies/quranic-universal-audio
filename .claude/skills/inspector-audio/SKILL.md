---
name: inspector-audio
description: Inspector audio subsystem — everything between bytes-on-disk and an `<audio>` element rendering audio in the browser, plus the extraction → bucket → inspector handoff that produces `reciters/<slug>/` in the first place. Covers both debugging AND building audio features.
---

# inspector-audio

Audio subsystem skill. Standalone — references below split by layer so the skill can grow new branches (per-codec, per-feature, per-platform) without bloating one doc.

Spans two arcs: the **runtime** path (bytes-on-disk → `<audio>`) and the **upstream handoff** (contributor source links → a reviewable `reciters/<slug>/` folder). The native align pipeline (Requests-tab Align, incl. online playlist intake) or offline Katana extraction writes the bucket content; `auto_detect` reconciles it into the lifecycle. See `references/extraction-intake.md`.

## Two corrections to hold (the docs used to lie about both)

1. **The CDN tier is a same-origin 200/206 stream, not a 302.** `audio_source.resolve` is three tiers — local Path → in-mem bytes → CDN — and the CDN tier is served by `_stream_cdn` same-origin with `Access-Control-Allow-Origin: *`. The old 302 was removed because it silenced `<audio crossorigin>` + the Web Audio kill-switch. There is **no disk-cache tier**.
2. **No prefetch worker, no GC sweeper, `_done.json` not read at runtime.** Bucket audio + peaks are written once — by the align pipeline's `acquire_audio` / `split_audio` HF jobs or by Katana extraction — and only **read** by the serving path. Nothing warms the bucket, nothing GCs it. The reconciler keys on the DB state row (`AWAITING_ALIGNMENT`), not the sentinel. "Audio missing on the bucket" is an extraction/upload problem.

## Topology

```
[upstream]  intake/edit request (DB requests row)
              ALIGN  = delivery_states.state == 'awaiting_alignment'
              INTAKE = requests status='pending' AND slug IS NULL  ─► Requests tab: /api/admin/intake/<rid>/plan → /align
                       (plan → mint reciter+delivery+slug, seed AWAITING_ALIGNMENT → align run)   [extraction-intake.md]
        │
        ▼
chapter URL (CDN, in catalog/audio_manifest/<slug>.json)
        │
        ▼
[writers]  align pipeline HF jobs (acquire_audio / split_audio) or Katana extraction (audio_persist.py + upload_to_bucket.py)
                                   ──►  bucket: reciters/<slug>/audio/<ch>.mp3      (Xing TOC injected if VBR)
                                   ──►  bucket: reciters/<slug>/peaks/<ch>.json.gz  (slim int8, schema v3)
                                   ──►  bucket: reciters/<slug>/audio/_done.json    (written last; offline audit only — NOT read at runtime)
        │                                       (read-only at runtime — no fetch worker, no GC sweeper)
        ▼
[reconcile] auto_detect: reciters/<slug>/ appears for an AWAITING_ALIGNMENT slug (keys on the DB state row)
                                   ──►  reciter.alignment_completed  →  AWAITING_REVIEW      [extraction-intake.md]
        │
        ▼
[backend]  audio_source.resolve(reciter, url)            (services/audio/audio_source.py — 3 tiers, no disk cache)
              ├─► local Path  (bucket mount)         ─► send_file (Range/ETag/304/sendfile)
              ├─► in-mem bytes (local-dev no-mount)  ─► send_file(BytesIO)
              └─► cdn_url                            ─► _stream_cdn: same-origin 200/206 stream + ACAO:* (NOT a 302)
        │
        ▼
[wire]    /api/seg/audio-proxy/<reciter>?url=…                                  (chapter MP3)           routes/audio/proxy.py
          ↳ FE only wraps when the CDN host fails the CORS+Range probe OR the file's first frame carries a `Xing` tag (lib/playback/play-url.ts + mp3-header.ts — Chrome TOC-seeks `Xing` files ±seconds; bucket remux is `Info`); other CORS-ok CDN files are played DIRECT
          /api/seg/segment-clip/<reciter>?url=…&start_ms=…&end_ms=…            (VBR fallback, ffmpeg -ss/-t -vn)  routes/audio/clip.py
          /api/seg/peaks/<reciter>?chapters=…&h=…                              (slim int8 envelopes)   routes/segments/peaks.py
          /api/audio/surahs/<cat>/<src>/<slug>                                 (dashboard player {url,duration_ms})  routes/audio/metadata.py
        │
        ▼
[frontend]  AudioPort → <audio> → (optional) MediaElementAudioSourceNode → GainNode → ctx.destination
                          (per-tab port; segPort for Segments, shared dashPort for Dashboard + Timestamps)
                          (file-absolute ms outside, clip-relative inside; element-pool gapless via shadow-audio.ts)
```

## Mode matrix

| Knob | Dev (`python3 inspector/app.py`) | Deployed (HF Space, gunicorn) |
|---|---|---|
| Audio source | bucket if mounted, else CDN stream-through every play | bucket NFS-mounted, sendfile via Path |
| Bucket | `hetchyy/quranic-inspector-bucket-dev` | bucket-dev (dev Space) / bucket (prod Space) |
| ffmpeg HTTPS reachability | full network | full network — image compiled with `--enable-openssl` + `file,pipe,http,https,tcp,tls` |
| Web Audio kill-switch | only fires once `ctx.state === 'running'` (post-warmup) | same |

VBR routing fork is **per-chapter**, not per-reciter. Decided by `audio_meta.is_vbr_for_url` server-side and the FE-shipped `reciter_vbr_chapters` list client-side. Same reciter can be CBR for chapter 1 and VBR for chapter 36.

## Reference index

| Reference | When to read | Key files |
|---|---|---|
| `references/extraction-intake.md` | The upstream handoff: align pipeline / Katana write `reciters/<slug>/` + audio-manifest sidecar, `auto_detect` fires `reciter.alignment_completed` → AWAITING_REVIEW, the three request kinds, ALIGN / INTAKE queues, the online intake plan → mint → align flow and the `intake.ingest()` mint | `services/segments/auto_detect.py`, `services/admin/intake.py`, `services/admin/intake_plan/`, `routes/admin/intake_plan.py`, `services/db/repo_requests.py`, `services/state/catalog.py`, `qua_shared/schemas/{intake_requests,intake_plan,catalog,state}.py` |
| `references/backend.md` | Proxy/clip/metadata routes, the 3-tier audio-source resolver (no disk cache), manifest sidecar + reverse index, storage paths, MIME, config tunables, the `/api/audio/surahs` route, `chapter_bitrate_kbps_for_reciter` | `routes/audio/{proxy,clip,metadata}.py`, `services/audio/{audio_source,audio_meta,audio_fetch}.py`, `services/storage/storage_paths.py`, `config.py` |
| `references/prefetch.md` | Bucket audio + peaks read-only at runtime: sole offline writer (Katana), read primitives, what's gone (removed prefetch worker + GC sweeper), and the FE-side warmups that replaced the deleted prefetch util | `services/audio/audio_fetch.py`, `routes/audio/proxy.py`, `routes/segments/peaks.py` |
| `references/peaks.md` | Slim int8 v3 envelope, `pack_slim`/`unpack_slim_envelope`, route fan-out + LRU response cache (NOT evicted on save), shared `b64ToInt8` decoder, `peaks-view.ts` shape adapter, history-peaks (now int8), backfill/audit | `services/audio/{peaks,peaks_slim,op_peaks,peaks_history}.py`, `routes/segments/peaks.py`, `lib/utils/{peaks-view,peaks-decode}.ts` |
| `references/vbr.md` | VBR-specific behavior — why Xing matters, Katana `_ensure_xing`, segment-clip fallback (`-vn` is load-bearing), AudioPort VBR reuse rule, VBR-only bug shapes | `.local/extraction/segments/audio_persist.py::_ensure_xing`, `routes/audio/clip.py`, `lib/playback/audio-port.ts` (VBR branch) |
| `references/frontend.md` | AudioPort (incl. `adoptElement`/`prewarm`/`covers`), AudioGraph kill-switch, warmup (in `main.ts`), AudioRange, shadow-audio element-pool gapless, the shared `dashPort`, peaks rendering, coordinate contract, cross-origin gotcha | `lib/playback/`, `lib/utils/audio-warmup.ts`, `tabs/segments/utils/playback/`, `tabs/segments/stores/playback.ts` |
| `references/bugs.md` | Common bug shapes — symptom → root → first probe, indexed by area | spans the whole stack |
| `references/probes.md` | Terminal recipes (ffprobe / ffmpeg / curl / hf bucket) + browser recipes (DevTools, Playwright) | — |

## Conventions

- File-absolute milliseconds outside `AudioPort`, clip-relative inside. Any caller writing `el.currentTime` directly is a bug.
- Bucket audio + peaks are written offline (**Katana extraction** `audio_persist.py` + `upload_to_bucket.py`) and only **read** at runtime — no in-Space fetch worker, no GC sweeper. Audio + peaks persist indefinitely. (FE-side `warmup.ts` / `shadow-audio.ts` are *browser* warmups — HTTP Range / element-pool — unrelated to the removed backend worker.)
- Manifest sidecar `catalog/audio_manifest/<slug>.json` is the **single source of truth** for VBR routing and chapter ↔ URL reverse lookup (cached as `_audio_manifest` + an O(1) `_audio_manifest_url_index`). Built offline by `scripts/audio/probe_audio_meta.py`. Never resolve chapter URLs through `detailed.json` — its per-entry `audio` field is `""` post-migration-#5.
- Style across references: terse, table-first, file-path-anchored. When the live filesystem drifts, fix the matching reference — don't add a new layer.
- **This skill is the ground truth for audio** — by design there is no `docs/reference/audio.md` (`docs/reference/README.md` carves audio out to here). The reference docs only *touch* audio as thin pointers: the route map in `architecture.md`, the playback stores in `frontend.md`, audio manifests in `catalog.md`. Keep those thin and consistent with this skill; the depth lives here.

## Bucket layout (audio-relevant)

```
catalog/audio_manifest/<slug>.json    # per-chapter URL + size + duration + bitrate_kbps + bitrate_mode
reciters/<slug>/audio/<chapter>.mp3   # Katana-written, Xing-injected if source is VBR
reciters/<slug>/audio/_done.json      # written last by extraction; offline audit/upload artifact only — NOT read at runtime
reciters/<slug>/peaks/<chapter>.json.gz   # slim int8 packed gzip (schema v3) — see references/peaks.md
```

Chapter keys: `"1"`..`"114"` for `by_surah`, `"<surah>:<ayah>"` for `by_ayah`. Audio + peaks persist indefinitely (no GC).

## What this subsystem deliberately doesn't do

No gapless / crossfade beyond the element-pool adopt (`shadow-audio.ts`, which is best-effort look-ahead, not sample-aligned crossfade). No HLS/DASH/adaptive bitrate. No DSP / EQ / loudness normalization (Web Audio gain is a kill-switch only). No on-the-fly transcoding beyond the Katana Xing inject + the segment-clip re-encode. No in-Space audio prefetch / re-bake / GC. rAF-bound boundary enforcement (~16 ms), not audio-clock-locked. Acceptable for review; not for sub-frame timing edits.
