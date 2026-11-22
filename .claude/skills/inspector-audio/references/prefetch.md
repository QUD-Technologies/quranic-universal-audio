# Bucket audio + peaks — read-only at runtime

There is **no in-Space prefetch worker and no GC sweeper** anymore. Bucket audio and slim peaks are written **offline** (Katana extraction only); the Inspector at runtime only **reads** them — nothing is downloaded to warm the bucket and nothing is deleted on a TTL. The whole `services/audio/audio_prefetch.py` module (sweeper daemon, `is_prefetched`, the `_done.json` sentinel reader) was removed, along with the `delivery_states.prefetch_purge_at` column.

> **Don't confuse this with FE "prefetch".** The browser-side `warmup.ts` (CBR HTTP-Range warm) and `shadow-audio.ts` (hidden element-pool for gapless) are *client* warmups — unrelated to the removed backend worker. They also replaced the deleted `tabs/segments/utils/playback/prefetch.ts` util (and its `prefetchEnabled` store / `insp_seg_prefetch` key). See `frontend.md`.

## Who writes bucket audio + peaks

| Artifact | Writer |
|---|---|
| `reciters/<slug>/audio/<chapter>.mp3` | `.local/extraction/segments/audio_persist.py` — Xing/Info header injected via `_ensure_xing` (`ffmpeg -c:a copy -f mp3`). |
| `reciters/<slug>/peaks/<chapter>.json.gz` | extraction only (`pack_slim`, schema v3 int8+gzip). See `peaks.md`. (There is **no** "timestamps job GEN_PEAKS pass" — that script/path is a phantom in old docs. One-shot CLIs `backfill_peaks_slim.py` / `convert_peaks_v2_to_v3.py` are the only other writers.) |

Chapter keys follow the audio-manifest sidecar: `"1"`..`"114"` for `by_surah`, `"<surah>:<ayah>"` for `by_ayah`. Audio + peaks persist indefinitely (both are cheap and back the Timestamps/Segments waveforms).

## Read primitives — `audio_fetch.py`

All resolve URL → chapter via `audio_meta.chapter_for_url` and return `None` when the URL isn't in the sidecar (caller falls back: CDN for audio, `/segment-peaks` ffmpeg for peaks):

| Function | Used by | Returns |
|---|---|---|
| `read_prefetched_audio_local_path(slug, url)` | audio-proxy (preferred — sendfile + Range/304) | `Path \| None` |
| `read_prefetched_audio_bytes(slug, url)` | proxy fallback for local-dev no-mount | `bytes \| None` |
| `read_prefetched_peaks(slug, url)` | `routes/segments/peaks.py::seg_peaks` | slim envelope `dict \| None` (raw, no dequant) |

## What's gone (do NOT document as live)

- The hourly **wip-audio sweeper** + the entire `audio_prefetch.py` module (`start_cleanup_daemon`, `_cleanup_loop`, `sweep_due`, `is_prefetched`), `audio_fetch.clear_prefetch`, the `INSPECTOR_WIP_SWEEPER` env, and the `delivery_states.prefetch_purge_at` column + its `admin.clear_prefetch_purge_at` transition/handler (migration `0011_drop_prefetch_purge_at`). Released reciters' audio + peaks are no longer GC'd.
- The `_done.json` sentinel is no longer read by the Inspector (`is_prefetched` is gone); extraction may still write it, but nothing routes on it.
- The original in-Space prefetch worker + FIFO queue + per-chapter fetch pipeline were removed earlier, when offline writers took over: Katana extraction for older reciters (Xing/Info injection in `audio_persist::_ensure_xing`, `-c:a copy -f mp3`), the align pipeline's HF jobs now (a canonical CBR re-encode, so no Xing pass).

> **Stale comments in live code:** `services/peaks.py` + `peaks_slim.py` docstrings still mention an `audio_fetch.fetch_and_persist_chapter` fallback writer that no longer exists. Ignore those — there is no runtime re-bake path.
