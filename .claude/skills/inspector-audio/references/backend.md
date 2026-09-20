# Backend audio plumbing

The route layer, the resolver, the manifest sidecar, and the storage-path builders. No worker logic (there is none — see `prefetch.md`). No peaks compute — that's `peaks.md`.

Current file layout (the old flat paths `routes/audio_proxy.py` / `routes/segment_clip.py` / `services/audio_source.py` etc. are gone):
- Routes: `routes/audio/proxy.py`, `routes/audio/clip.py`, `routes/audio/metadata.py`, `routes/segments/peaks.py`
- Services: `services/audio/{audio_source,audio_meta,audio_fetch}.py`, `services/storage/storage_paths.py`

## Audio proxy route

`routes/audio/proxy.py` — `seg_audio_proxy(reciter)`, blueprint `audio_proxy_bp` (`url_prefix=/api/seg`). `/api/seg/audio-proxy/<reciter>?url=<encoded>`, single GET, three-tier resolve.

| Tier | Source | Served as |
|---|---|---|
| 1 | bucket file (`reciters/<slug>/audio/<chapter>.mp3`) at a real local Path | `send_file(path, conditional=True, etag=True, last_modified=…)` — Werkzeug handles Range + 304 + sendfile |
| 2 | local-dev bucket bytes (no mount) | `send_file(BytesIO(bytes), conditional=True, etag=…)` — still seekable |
| 3 | CDN | **`_stream_cdn`: same-origin 200/206 byte-stream + `Access-Control-Allow-Origin: *`** (64 KB chunks). **NOT a 302** — a 302 to the CDN returns an opaque cross-origin response that silences `<audio crossorigin>` + the Web Audio kill-switch. |

Cache-Control on tiers 1+2: `public, max-age=31_536_000, immutable`. `Accept-Ranges: bytes` advertised so the browser switches to byte-range fetches after the first response. MIME via `AUDIO_MIME_TYPES.get(suffix, "audio/mpeg")`.

Optional `?download=1` (+ `&chapter=<n>`) makes every tier emit `Content-Disposition: attachment; filename="<reciter>-<NNN>.mp3"` (`_download_name` sanitizes the slug + zero-pads the chapter) — drives the dashboard footer's download button. Absent → inline streaming.

> The download-all / delete-cache / cache-status endpoints, the in-Space prefetch worker, AND the hourly GC sweeper have all been removed. The only audio-adjacent daemon is the `auto_detect` reconciler loop (60 s) — see `extraction-intake.md`. Bucket audio is written exclusively by Katana extraction; there is no admin re-trigger endpoint.

## Segment-clip route

`routes/audio/clip.py` — `seg_segment_clip(reciter)`, `segment_clip_bp` (`/api/seg`). VBR per-segment fallback. See `vbr.md` for the full treatment. Two load-bearing details:
- **`-vn` is required** in the ffmpeg cmd. The stripped static ffmpeg has no png encoder; an mp3-mux of a file with embedded APIC cover-art fails to "200 OK / 0 bytes" without it.
- **Allowlist is sidecar-keyed**, not `detailed.json`: `_is_known_chapter_url(reciter, url) = audio_meta.chapter_for_url(reciter, url) is not None` + scheme ∈ {http, https}.

## Audio metadata route (dashboard player)

`routes/audio/metadata.py` — `audio_surahs(...)`, `audio_meta_bp` (`url_prefix=/api/audio`). `GET /api/audio/surahs/<category>/<source>/<slug>` returns the manifest-owned per-chapter `{url, duration_ms}` for the dashboard `BottomPlayer`. The route must never substitute another recording because timestamps, peaks, and bitrate metadata share the manifest audio's timebase. (Distinct from the seg-tab routes; easy to miss.)

`duration_ms` is derived from the manifest's `duration_sec`. **Fallback:** when a chapter's manifest duration is null/missing, the route reads the duration baked into the slim peaks header (`reciters/<slug>/peaks/<ch>.json.gz`) via `audio_fetch.read_prefetched_peaks_duration_ms` — so a reciter probed before the manifest carried durations still shows a real scrubber length instead of 0:00. Stays `null` only when peaks are also absent.

## Audio source resolver

`services/audio/audio_source.py` is the single chokepoint every backend audio consumer (proxy, peaks decode, clip) goes through. Answers two questions for a `(reciter, url)` pair: *where do this chapter's bytes live right now?* and *is this chapter VBR?*

```python
@dataclass(frozen=True)
class AudioSource:
    cdn_url: str
    data: Optional[bytes]
    path: Optional[Path]
    vbr: bool
    bitrate_kbps: Optional[int]
    chapter_key: Optional[str]

    @property
    def has_local_bytes(self) -> bool:
        return self.data is not None or self.path is not None
```

Priority inside `resolve()` — **three tiers, no disk cache**:

1. Bucket **local Path** (`audio_fetch.read_prefetched_audio_local_path`) — preferred so callers stream via send_file/sendfile, not pull 4–5 MB into Flask memory.
2. Bucket **bytes** (`audio_fetch.read_prefetched_audio_bytes`) — only useful in local-dev with no mount.
3. CDN URL only — `has_local_bytes` is False. ffmpeg can still decode this directly via its HTTPS-enabled build; local sources are preferred only for latency.

`vbr` and `bitrate_kbps` come from `audio_meta.chapter_meta_for_url`. (There is **no** `audio_cache_path` disk-cache tier and **no** `resolve_chapter_peaks` pass-through — both are phantom in old docs. Peaks are read directly via `audio_fetch.read_prefetched_peaks`.)

**ffmpeg + network:** the image's ffmpeg is compiled with HTTPS support (`--enable-openssl` + `file,pipe,http,https,tcp,tls`). Any backend that needs a remote chapter can hand ffmpeg the URL directly — ffmpeg does its own frame-aware, VBR-correct HTTP Range fetches. Local sources are still preferred when available, purely for latency.

## Manifest sidecar

`catalog/audio_manifest/<slug>.json` — built offline (the audio-probe pipeline lives in `.local/`), single source of truth for chapter↔URL routing and VBR mode. On first access `audio_meta._load_sidecar` populates `cache._audio_manifest` (the entries) **and** derives `cache._audio_manifest_url_index` (a `{url: chapter_key}` reverse map) so `chapter_for_url` is O(1) instead of a per-request linear scan.

Entry shape (`<chapter_key> → entry`):

```json
{"url": "...", "size_bytes": ..., "duration_sec": ..., "bitrate_kbps": ..., "bitrate_mode": "vbr"|"cbr"}
```

Keys: `"1"`–`"114"` for by_surah, `"<surah>:<ayah>"` for by_ayah.

| Function | Returns |
|---|---|
| `chapter_meta(reciter, chapter)` | sidecar entry by chapter key |
| `chapter_meta_for_url(reciter, url)` | sidecar entry by URL — reverse lookup |
| `chapter_for_url(reciter, url)` | raw sidecar key for a URL (`"1"`–`"114"` or `"<s>:<a>"`) — O(1) via `_audio_manifest_url_index`; used by audio-proxy + peaks short-circuit + clip allowlist |
| `is_vbr(reciter, chapter)` / `is_vbr_for_url(reciter, url)` | chapter-keyed and URL-keyed variants |
| `chapter_urls(reciter)` | full chapter-key → URL map — drives the `/peaks` route's URL enumeration |
| `chapter_numbers(reciter)` | sorted chapter numbers present in the sidecar |
| `vbr_chapters_for_reciter(reciter)` | sorted list of VBR chapter numbers — shipped to the FE as `reciter_vbr_chapters` so cross-chapter accordion prefetch picks the transport without per-chapter probes. by_surah only (non-int keys skipped). |
| `chapter_bitrate_kbps_for_reciter(reciter)` | per-chapter kbps map — shipped to the FE to drive `audio-warmup`/`warmup.ts` Range-prefetch byte offsets. by_surah only. |

## Storage paths

All bucket keys built by `services/storage/storage_paths.py` — never construct these strings inline:

| Function | Path |
|---|---|
| `audio_manifest_path(slug)` | `catalog/audio_manifest/<slug>.json` |
| `prefetched_audio_path(slug, chapter)` | `reciters/<slug>/audio/<chapter>.mp3` |
| `prefetched_peaks_dir(slug)` | `reciters/<slug>/peaks` |
| `prefetched_peaks_path(slug, chapter)` | `reciters/<slug>/peaks/<chapter>.json.gz` (slim int8) |
| `prefetched_peaks_legacy_path` / `_backup_path` | `…/<ch>.json` / `…/<ch>.json.bak` (backfill/rollback only) |
| `timestamps_path(slug, ch)` | `reciters/<slug>/timestamps/<ch>.json` |

> `prefetched_audio_dir` and `prefetch_done_marker_path` do **not** exist (phantom in old docs). `_done.json` is written by extraction and read only by offline audit/upload scripts — never at runtime.

## MIME mapping

`AUDIO_MIME_TYPES` in `config.py` maps suffix → MIME for the proxy. Default fallback: `audio/mpeg`. Add new entries here when supporting a new container.

## Tunables (`config.py`)

| Const | Value | Notes |
|---|---|---|
| `AUDIO_CACHE_MAX_AGE` | 31_536_000 | 1-year `immutable` for audio + hashed peaks. |
| `FFMPEG_TIMEOUT` | 15 s | Segment-peaks decode. |
| `FFMPEG_FULL_TIMEOUT` | 300 s | Full-file peaks decode + segment-clip. |

**Removed config (don't reintroduce):**
- `CACHE_DIR` and the whole disk-cache layer — gone; the resolver has no disk-cache tier.
- Python ID3v2 + byte-arithmetic Range decoder (deleted when ffmpeg gained HTTPS): `ID3_PROBE_BYTES`, `DEFAULT_BYTES_PER_SEC`, `RANGE_DECODE_PAD_SEC`, `cache.{get,set}_url_audio_meta`. Let ffmpeg handle the network.
- In-Space prefetch worker + GC sweeper: `AUDIO_DL_WORKER_COUNT`, `INSPECTOR_AUDIO_PREFETCH`, `INSPECTOR_WIP_SWEEPER`, `delivery_states.prefetch_purge_at`. See `prefetch.md` "what's gone".

> **Stale comments in live code (fix on sight):** `peaks.py`, `peaks_slim.py`, and `config.py` docstrings still mention an `audio_fetch.fetch_and_persist_chapter` "recompute/rebake fallback writer" — that function was **deleted**. There is no runtime peaks-rebake path (one writer: Katana, plus one-shot CLIs). `cache.py:391-393` claims the peaks LRU is invalidated on save — it is not.

## Why no Flask in `services/`

Backend services are Flask-free by convention. `audio_source`, `audio_meta`, `audio_fetch`, `peaks` import from `services` / `qua_shared` only. The route layer (`routes/audio/*.py`, `routes/segments/peaks.py`) is the thin parse → service → jsonify layer that holds Flask. New audio route → blueprint under `routes/audio/` (or `routes/segments/` for peaks), registered in `routes/__init__.py::register_blueprints`.
