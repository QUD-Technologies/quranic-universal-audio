---
name: upstream-drift
description: Batch-check every released / under-review / awaiting-review / awaiting-alignment delivery for upstream audio that drifted from the bucket copy (a CDN replaced or re-encoded a chapter under the same URL) and report it. Use when audio is out of sync on one chapter while segments look right, after a source CDN change, or as a periodic audit.
---

# Upstream drift audit

A source CDN can swap a chapter for another file under the same URL. The bucket copy,
segments and timestamps keep the original. Playback is safe — `play-url.ts` plays the CDN
directly only when its size equals the manifest `size_bytes`, else the bucket copy through the
proxy — but drift still means the source no longer backs the delivery (re-intake, provenance,
releases that link upstream).

## Run

Read-only against the bucket (state DB, manifests, one listing per delivery) and upstream (two
small range reads per chapter). The full prod fleet takes roughly 10–20 min; run it in the
background.

```bash
python scripts/diagnostics/upstream_drift.py --bucket prod --json drift.json --md drift.md
python scripts/diagnostics/upstream_drift.py --bucket prod --states released --slug <slug> --slug <slug>
```

Default states: `released,under_review,awaiting_review,awaiting_alignment`. Exit code 1 when
any chapter drifted. Skipped: YouTube / Drive / SoundCloud sources, combined-file chapters
(`source_url` set) and non-http urls.

## Verdicts

| Verdict | Meaning | Action |
|---|---|---|
| `recording` | size and duration changed — a different recording | Report to the user: the source no longer matches the alignment; playback already falls back to the bucket copy |
| `reencode` | same duration, different bitrate / sample rate | Bucket copy is the better audio; nothing to fix in the Inspector |
| `tag_edit` | under 64 KB size change, same duration | Ignore (metadata) |
| `resized` | size changed, same duration and format | Look at one file before acting |
| `gone` | upstream 404 / 410 | Bucket copy is now the only copy |
| `error` | network / parse failure, or no manifest | Re-run the slug; repeated errors are a host problem |

Expected size is the manifest `size_bytes`; a manifest without it is compared with the bucket
copy's size, which can differ by a few bytes from the source (shows as `tag_edit`).

## Report

Give the user the verdict counts, the per-delivery drift table and the `recording` / `gone`
chapters with their then → now size, duration and format. Do not edit manifests, bucket audio
or delivery state from this audit: any fix (re-intake, manifest backfill) is a prod write that
needs the user's go.

Related: `inspector-audio` skill (`references/bugs.md`, the CDN-swap row) for the playback side.
