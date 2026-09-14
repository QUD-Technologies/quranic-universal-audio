---
name: inspector-playwright
description: Screenshot the real Timestamps analysis view (UnifiedDisplay) for any reciter:surah:verse straight from shards — fast, drift-free, no SPA/audio/OAuth.
---

# inspector-playwright

Screenshot the **real** Timestamps analysis view (`UnifiedDisplay`) for a
`reciter : surah:verse` straight from shards. No SPA navigation, no audio, no
OAuth, no progress-bar pixel math — and **drift-free**: the harness
(`inspector/frontend/harness/analysis.{html,ts}`) *imports* the production
component + the real `ts-source` assembly (`assembleVerseFromShard`), so a
prop/assembly change fails the build rather than silently diverging. Far faster
and more reliable than driving the live SPA or a local Flask (which dies
silently in long sessions).

## Run

From `inspector/frontend` (so `vite` + `playwright` resolve from its `node_modules`):

```
node ../../.claude/skills/inspector-playwright/scripts/shoot.mjs \
    --reciter <slug> --ref 45:32 [--ref 11:57 …] \
    [--words 1-3] [--out DIR] [--api https://…hf.space] [--port 5199]
```

- `--reciter` — slug, e.g. `nasser_al_qatami_mp3quran` (find it from the manifest / the Timestamps surah-picker search).
- `--ref` — `surah:verse[@occasion]`, repeatable; one PNG per ref → `<out>/<slug>_<s>-<v>[_occN].png`. A verse the reciter broke mid-way has one occasion per run, and a stop sign lives on an occasion's **last** boundary — `--ref 2:26@3` shoots the third run at that verse (default the first).
- `--words a-b` — optional 1-based **inclusive** word range, for a narrow crop of a long verse.
- `--api` — backend the harness fetches shards from. Default is the **dev Space** (`https://hetchyy-quranic-inspector-dev.hf.space`): its shard endpoint is public (no auth) and `no-store` (always fresh after a re-stamp), so no local Flask is needed. Point at `http://127.0.0.1:5000` only when a local backend is up.
- `--out` — output dir (default cwd). `--port` — Vite port (default 5199).

## How it works

`shoot.mjs` boots Vite programmatically with `INSPECTOR_API_TARGET=<api>` (which
the Vite config turns into an `/api` proxy at that origin), drives headless
Chromium to `…/harness/analysis.html?reciter&ref`, waits for `body[data-ready]`,
and screenshots `#app`. The harness fetches manifest/shard/qpc/dk via the real
loaders, builds `TsVerseData` with `assembleVerseFromShard`, sets the real
`loadedVerse` store, and mounts `UnifiedDisplay` — the exact app render path.
It also mirrors the tab's edition plumbing: `wordProfile` / `deliveryRiwayah`
off the shard's `_meta`, `--font-quran` + `ensureEditionFont` for the face, and
`WordTimedRow` instead of `TimedAnalysisRow` for a word-profile (non-Hafs)
shard — so a projected edition renders in its own script, not DigitalKhatt.

## Show the result in chat

After the PNG is written, **display it inline in chat by `Read`-ing the PNG path**
(the Read tool renders images) — don't just report the path. One Read per shot.

## Notes

- Both tiers render by default (the app defaults the **phoneme row OFF**; the harness turns it ON since the letter↔phoneme alignment is the point). Opt out with `&letters=0` / `&phonemes=0` on the harness URL.
- After a re-stamp the shot is immediately fresh (shard endpoint is `no-store`); there is no browser/CDN cache to bust. → [[shard-browser-cache-stale]]
- The dev-only perf-A/B HUD is hidden by the harness CSS.
- The screenshot is the analysis (letter ↔ phoneme cell) view only — to verify tajweed underlines / merger badges / silent greys after a phonemizer or qua-sdk change, this is the tool.
