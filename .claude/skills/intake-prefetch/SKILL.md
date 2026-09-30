---
name: intake-prefetch
description: Run the first part of an online intake (listing + audio acquire) locally when the HF side can't — YouTube bot-checks from HF IPs, flaky Drive/archive.org fetches, wrong-content source files — then hand the run back to the Inspector for GPU align. Includes cleanup.
---

# intake-prefetch

The align pipeline is: plan (list the source) → mint → **acquire** (HF Job) → align → split → sidecars → assemble. Ref: `docs/reference/align-pipeline.md`. This skill covers doing **plan listing** and **acquire** on this machine, then resuming online.

Env for every snippet: `HF_TOKEN` from the main checkout's `.env`; prod bucket `hetchyy/quranic-inspector-bucket`; prod API `https://hetchyy-quranic-universal-audio.hf.space`. In Git Bash prefix curl calls to `/api/...` with `MSYS_NO_PATHCONV=1`. Work in the session scratchpad, never the repo.

## When to use

| Symptom | Cause | Do |
|---|---|---|
| Build plan fails `could not list …` / `Sign in to confirm you're not a bot` | YouTube blocks HF/AWS IPs (listing **and** download), all clients; cookies rot within hours | §1 + §2 |
| Acquire fails `BotCheckError` for YouTube sources | same | §2 |
| Acquire fails on Drive (`quota exceeded`, 403) or archive.org 5xx | host throttling; HTTP 5xx is already retried 4× in-job | wait + **Retry** once; still failing → §2 |
| Coverage report lists a surah as missing / a file "repeats surah N" | a source file holds the wrong surah (e.g. two files with the same audio) | §4 |

The durable fix for YouTube is a residential proxy in Space secret `INSPECTOR_YTDLP_PROXY` — if set, just Retry online instead.

## 1. List locally (plan)

Home IPs list fine without cookies:

```python
import dataclasses, json, requests
from qua_shared.schemas import IntakeSource
from services.admin.intake_plan.enumerate import enumerate_source   # sys.path: repo root + inspector/
listing = enumerate_source(IntakeSource(method="playlist", playlist_url=URL))
body = {"listing": dataclasses.asdict(listing)}
r = requests.post(f"{API}/api/admin/intake/{RID}/plan", json=body,
                  headers={"Authorization": f"Bearer {HF_TOKEN}"})   # owner bearer works on intake/align routes
```

The request list route rejects the bearer; find open requests (`kind`, `payload.source`, `reciter_id`) in a downloaded copy of `db/inspector.db`. If prod rejects the listing with `extra_forbidden` on a field your branch added (e.g. `entries[].chapters`), drop it when empty and re-post.

Then fill the identity (§1a), and in Requests → the plan panel set includes, press **Align** (`POST /api/admin/intake/<rid>/align` with `{"device": "GPU"}` takes the bearer too). The run starts and acquire fails on the blocked sources — expected; it has now written `staging/<slug>/<run_id>/groups.json`.

## 1a. Identity: country + recording year

Every plan whose identity lacks `country` or `recording_year` gets researched before Align — don't mint blanks.

- **Recording year**: upload dates of the first, middle and last entry — `yt-dlp --skip-download --playlist-items 1,57,114 -J <playlist>` → `upload_date`; playlist `modified_date` via `--flat-playlist -J`. Uploads all inside one calendar year → that year (Saudi Center for Quranic Recitations: Majed Al-Zamil 28 Apr – 1 May 2025, Al-Sudais May – Jul 2026). Uploads spanning years (Badr Al-Turki: May 2025 – Jul 2026) are not a recording year — leave it empty. Read the video description too: it can name the recording body or date.
- **Country**: web search the reciter (Arabic name + "القارئ"); the channel's own description may state it (the Saudi Center channel says it belongs to the Saudi Broadcasting Authority). Reciter pages on tvquran / surahquran / islamway usually carry it.
- **Where each value lands**:
  - `country` is written by mint **only for a new reciter**. For an existing reciter with no country, edit the reciter in the catalog (Admin → Catalog; `PATCH /api/admin/catalog/reciter/<id>` needs a signed-in session — the bearer is refused).
  - `recording_year` in the plan identity also goes into the slug (`<reciter>_2025_yt`). The Saudi Center deliveries keep the plain `<reciter>_yt` slug and carry the year as a delivery field (`abdulaziz_al_turki_yt` = 2025): set it with a catalog delivery edit after mint, unless the user wants the year in the slug.
- Report the findings with sources, then apply them.

## 2. Acquire locally

1. Download `staging/<slug>/<run_id>/groups.json` **and** `catalog/audio_manifest/<slug>.json` into a local mount dir `M/` at the same relative paths (the job reads both). On Windows the scratchpad path blows MAX_PATH — make `M` a short junction (`New-Item -ItemType Junction C:\Users\<you>\qim -Target <scratchpad>\M`) and remove it at cleanup.
2. Run the real job against that dir (it fetches, encodes to canonical mp3, bakes peaks):
   ```bash
   SLUG=<slug> RUN_ID=<run_id> INSPECTOR_BUCKET_MOUNT=M PYTHONPATH=<repo root> ACQUIRE_WORKERS=2 python qua_jobs/acquire_audio.py
   ```
   Keep it to **one slug at a time, `ACQUIRE_WORKERS` 1–2**: three slugs × 2 yt-dlp tripped a bot check on a home IP, and after the first `BotCheckError` the job skips every remaining source. Any other YouTube traffic from the same IP counts — do the §1a `yt-dlp -J` lookups before or after acquire, never during (a lookup alongside a 2-worker run tripped it after 7 files). A probe (`yt-dlp --simulate -f 140 <url>`) passing again means re-run. A video that dies mid-stream (`bytes read, more expected`) usually works with `yt-dlp -f 140`; transient 403s just need a re-run (the job skips finished files). A killed run leaves `audio/*.mp3.part` — delete them.
3. Upload `M/reciters/<slug>/audio/*.mp3` (+ `peaks/*.json.gz` for single-chapter sources) to the same paths in the prod bucket with `huggingface_hub.batch_bucket_files(add=[(local_path, dest)])` — pass **file paths, not bytes** (bytes overwrite is ~25× slower). Check `df` first: a mushaf is ~2.5 GB. When space is tight, upload in chunks, verify each remote size (`fs.invalidate_cache()` before `fs.info`), then truncate the local file to 0 bytes — the job's skip check is `is_file()`, so it still counts as done. Finally list the bucket dir and confirm all slots exist.
4. Inspector → **Retry** the run. Acquire sees every file present and skips; align proceeds on GPU. If the user asked for **audio only**, stop before Retry and say so — Retry starts align.

Slots: a combined source lands in `audio/<200 + position among INCLUDED plan entries>.mp3`. Changing includes after prefetch shifts slots — re-run §2.

## 3. Watch

`GET /api/admin/reciter/<slug>/align/status` (bearer ok) until `done/succeeded`. A failed stage → read `staging/<slug>/<run_id>/{acquire,split}.json`, fix, **Retry** (resumes from the failed stage).

## 4. Wrong-content source file

Upstream mirrors can share a bad file (e.g. `046` actually holding surah 47). Don't cross-source to YouTube offsets in the manifest — releases need a real per-chapter link:
1. Find the right recording (another upload / juz video of the same session); verify by cross-correlation and aligner probe.
2. Upload the corrected file to a QUD archive.org item (the built-in browser pane is signed in; the user publishes/approves).
3. Align that one chapter and splice it into the bucket files (`detailed`, `segments`, `low_confidence_v2`, `auto_split_v1`, `chapter_sources`, `coverage_report`, `pipeline_meta`, edit histories, `catalog/audio_manifest/<slug>.json` with `source_url` + recomputed checksum). Keep each file's exact JSON format; one bucket write per process; verify by re-download.
4. DB delivery `chapter_count` / `total_duration_sec`: the PATCH route rejects bearer — use an inspector-admin `_bootstrap.run(..., safe_write=True)` one-off with `--prod --yes-prod` and `INSPECTOR_DEV_OWNER_HF_ID` / `_LOGIN` set (pauses + resumes the Space itself).
5. Restart the prod Space if you wrote the bucket out-of-band, then check `/api/seg/chapters/<slug>`, `/api/seg/validate/<slug>` (`missing_verses` 0) and the audio proxy.

## Cleanup

- Delete the local mount dir, the junction, the downloaded DB copy and any source audio / probes from the scratchpad (on Windows use `rd /s /q \\?\<path>` for long paths) and leftover `%TEMP%\acq_*` dirs.
- Never leave a cookies file on disk; never extract browser cookies.
- Stop any local helper servers or background downloads you started.
- Don't delete `staging/` or `audio/2xx.mp3` slots yourself — split removes the slots once every cut succeeds; a failed split keeps them for Retry.
