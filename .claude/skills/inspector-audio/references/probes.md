# Probe recipes

Reproducible commands for inspecting the audio subsystem from outside. Backend probes are read-only and safe to run unprompted; frontend probes require a running dev server (`python3 inspector/app.py` + optionally `cd frontend && npm run dev`).

Replace `<slug>` / `<reciter>` / `<chapter>` / `<urlencoded>` per case. Bucket commands assume the dev bucket `QUD-Technologies/quranic-inspector-bucket-dev` unless explicitly otherwise.

## Bucket inspection

```bash
# Manifest sidecar — what the backend thinks about a reciter's encoding mix
hf bucket cat QUD-Technologies/quranic-inspector-bucket-dev/catalog/audio_manifest/<slug>.json \
  | jq '.chapters | to_entries
        | map({k:.key, mode:.value.bitrate_mode, kbps:.value.bitrate_kbps, dur:.value.duration_sec})'

# Bucket-audio state for a reciter (written by Katana extraction, not the Space)
hf bucket ls QUD-Technologies/quranic-inspector-bucket-dev/reciters/<slug>/audio/
hf bucket cat QUD-Technologies/quranic-inspector-bucket-dev/reciters/<slug>/audio/_done.json

# Peaks blob for a chapter (slim int8, gzipped — must gunzip before jq)
hf bucket cat QUD-Technologies/quranic-inspector-bucket-dev/reciters/<slug>/peaks/<chapter>.json.gz \
  | gzip -d | jq '{duration_ms, bps, n, q}'

# Sample multiple reciters' encoding modes at once (run after listing)
for slug in alghazali alhusary minshawi; do
  echo "== $slug ==";
  hf bucket cat QUD-Technologies/quranic-inspector-bucket-dev/catalog/audio_manifest/$slug.json \
    | jq -r '.chapters | to_entries | map(.value.bitrate_mode) | group_by(.) | map({mode:.[0], n:length})';
done
```

## ffprobe / ffmpeg on bucket files

```bash
# Full probe — duration, bitrate, channel layout, codec
ffprobe -v error -show_format -show_streams -of json <local-mount>/reciters/<slug>/audio/<chapter>.mp3 | jq '.format, .streams[0]'

# VBR + Xing TOC presence (look for Xing/VBRI/Info tags in stderr)
ffmpeg -i <file> 2>&1 | grep -iE 'xing|vbri|info'
# Present ⇒ seekable. Absent + manifest says VBR ⇒ Katana _ensure_xing failed
# (re-run extraction; there is no in-Space remux to retrigger).

# Reproduce Katana's _ensure_xing (audio_persist.py) — mp3 muxer auto-writes the
# Xing/Info header when output is seekable. NOT `-bsf:a mp3_to_xing` (non-existent filter).
ffmpeg -y -i <raw-vbr.mp3> -c:a copy -f mp3 -v error /tmp/remuxed.mp3
ffmpeg -i /tmp/remuxed.mp3 2>&1 | grep -iE 'xing'

# Reproduce the segment-clip route (-vn is load-bearing: stripped ffmpeg has no png
# encoder, so a source with embedded APIC cover-art muxes to 0 bytes without it)
ffmpeg -hide_banner -loglevel error \
       -ss 12.345 -i <file> -t 4.500 \
       -vn -c:a libmp3lame -b:a 96k -ac 1 -f mp3 - | wc -c

# Reproduce full-file peaks decode (raw PCM, count samples)
ffmpeg -i <file> -f s16le -ac 1 -ar 8000 -v quiet - | wc -c
# samples = bytes / 2; duration_ms = samples / 8 (Hz=8000 → 8 samples/ms)
```

## HTTP probes through the running app

```bash
# Audio proxy with byte range (expect 206 Partial Content)
curl -i -H 'Range: bytes=0-99' \
  'http://localhost:5000/api/seg/audio-proxy/<reciter>?url=<urlencoded>' \
  | head -20

# Verify Accept-Ranges + immutable cache
curl -I 'http://localhost:5000/api/seg/audio-proxy/<reciter>?url=<urlencoded>'

# Segment clip — pull a single window to a file
curl -o /tmp/clip.mp3 \
  'http://localhost:5000/api/seg/segment-clip/<reciter>?url=<urlencoded>&start_ms=12345&end_ms=16789'
ffprobe -v error -show_entries format=duration /tmp/clip.mp3

# Full-file peaks — response is {"peaks": {<url>: <int8 envelope>}, "complete": true};
# each value is a slim envelope, NOT a float array
curl -s 'http://localhost:5000/api/seg/peaks/<reciter>?chapters=36&h=abc' \
  | jq '.peaks | to_entries[0].value | {q, n, bps, duration_ms}'

# Dashboard player metadata (distinct route family)
curl -s 'http://localhost:5000/api/audio/surahs/by_surah/<source>/<slug>' \
  | jq '.surahs | to_entries[0]'

# Health probe — bucket mount + boot status
curl -s http://localhost:5000/healthz | jq
```

## URL encoding helper

```bash
python3 -c 'import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1], safe=""))' 'https://...'
```

## Audit query — historic prefetch/sweeper events for a slug

```bash
# Per-month partition: audit/<YYYY-MM>.jsonl
# All `audio_prefetch.*` events are HISTORIC — the prefetch worker and the GC
# sweeper have both been removed, so no new ones are emitted. Old entries stay
# queryable.
hf bucket cat QUD-Technologies/quranic-inspector-bucket-dev/audit/$(date +%Y-%m).jsonl \
  | jq -c "select(.slug == \"<slug>\" and (.event | startswith(\"audio_prefetch\")))"
```

## Frontend / browser probes

### Setup — expose the port for inspection

**`__segPort` is NOT exposed by default** — the console recipes below won't work until you add this dev-only line yourself (gate with `import.meta.env.DEV`) and reload:

```ts
// tabs/segments/stores/playback.ts  (Segments)
if (import.meta.env.DEV) (window as any).__segPort = segPort;
// or, for Timestamps/Dashboard, expose the SHARED dashPort:
// lib/playback/dash-port.ts → (window as any).__dashPort = dashPort;
```

Timestamps + Dashboard run on `dashPort`, **not** `tsPort` (which is vestigial). Diagnostic trace logs: `localStorage.setItem('insp_warmup_log','true')`.

### DevTools console (segments tab loaded)

```js
// AudioContext state — should be 'running' after first gesture
(await import('/src/lib/playback/audio-graph.ts'))._getCtx()?.state

// AudioPort introspection
__segPort.window                // current LoadedWindow {startMs, endMs, offsetMs, src, isClip}
__segPort.currentTimeMs()       // file-absolute ms
__segPort.source                // {audioUrl, cbrSrc, reciter, vbr}
__segPort.element.readyState    // 0..4 (HAVE_NOTHING..HAVE_ENOUGH_DATA)
__segPort.element.networkState  // 0..3
__segPort.element.error         // MediaError | null
__segPort.element.buffered      // TimeRanges

// Force a controlled load + play to repro a bug deterministically
__segPort.setSource({ audioUrl: 'https://...', reciter: '<slug>', vbr: true });
const r = __segPort.loadCovering(12345, 16789);
await r.ready;
__segPort.seekAndPlay(12345);
```

### Playwright MCP recipes

```
browser_navigate          http://localhost:5000/?tab=segments&reciter=<slug>
browser_wait_for          (text or selector confirming segments tab loaded)
browser_evaluate          (() => __segPort?.window)
browser_console_messages  (filter for audio-related errors)
browser_network_requests  (filter URL contains '/api/seg/')
browser_take_screenshot   (visual confirmation)
```

### Network waterfall, audio-only

DevTools → Network → filter `audio-proxy|segment-clip|peaks`. Sort by Time. Look for:

- Slow/long `audio-proxy` responses (200/206, **not** 302) — bucket miss, falling through to tier-3 `_stream_cdn` (proxies from the CDN same-origin).
- 503s / 5xx on `segment-clip` — ffmpeg failure (timeout, missing source).
- Peaks requests without `?h=` query string — caller not passing the hash, will get 1-day cache instead of 1-year.
- Repeat segment-clip fetches for the same `(url, start_ms, end_ms)` triple — clip URL not deterministic, or browser HTTP cache busted.

## Multi-reciter sampling

Audio bugs frequently reproduce on one codec/bitrate combo and not another. Sample at minimum across the CBR/VBR axis:

```bash
# Pull a small sample of slugs across the encoding axis
hf bucket ls QUD-Technologies/quranic-inspector-bucket-dev/catalog/audio_manifest/ | head -20
# Then for each, check the encoding mix per the jq snippet above.
```

For routing bugs: include at least one by_ayah delivery (key shape `"<surah>:<ayah>"`) — many code paths special-case integer keys and silently miss the by_ayah variant.

## When to add a new probe

When you find yourself running the same multi-step probe twice in a debugging session, save it here. Keep the recipe self-contained — no external scripts, no helper functions, copy-pasteable into a fresh shell or DevTools console.
