# Catalog Reference

> **Storage**: the catalog is **SQLite** (`inspector.db`, tables read/written via `inspector/services/db/repo_catalog.py`), uploaded whole-file to the bucket after each committed write (`services/db/sync.py`). It was `catalog/reciter_catalog.json` pre-migration; that JSON backup (`storage_paths.catalog_path()`) is now **dead — no app reads it** (the DB is the catalog source of truth; the whole-bucket validator deliberately skips it) and is **slated for removal**. **Audio metadata sidecars stay JSON on the bucket** at `catalog/audio_manifest/<slug>.json` (`storage_paths.audio_manifest_path`), read via `services/audio/audio_meta.py`.

Layers, slug convention, schema, audio-metadata split, naming guide, and add/probe/maintain procedures.

## 1. Where things live

| Concern | Location | Writer |
|---|---|---|
| vocab / reciters / deliveries / aliases / derived | SQLite tables (see §4 col map) | `repo_catalog.py` (via `services/state/catalog.py`) |
| Read model (`ReciterCatalog`) | `repo_catalog.snapshot()` → cached on `db_seq` by `services/state/catalog.py::snapshot()` | — |
| Pydantic shapes (runtime authority) | `qua_shared/schemas/bucket/catalog.py` | — |
| Per-delivery audio sidecar | `catalog/audio_manifest/<slug>.json` (bucket JSON) | offline probe `scripts/audio/probe_audio_meta.py` |
| Public read endpoint | `/api/static/catalog.json` (`routes/public/static.py`) — serializes the viewer projection of `snapshot()` | — |

`repo_catalog.snapshot()` reassembles the full `ReciterCatalog` so the public JSON stays byte-identical to the pre-migration file (migration parity gate diffs `model_dump(by_alias=True)`). Sidecars are 5–20 KB each for `by_surah` (114 chapters), larger for `by_ayah` (6236 ayahs).

## 2. The three layers

| Layer | Identity question | Where it lives |
|---|---|---|
| **Reciter** | Who is the human? | `reciters` table row, keyed by `reciter_id` |
| **Recording** | Which artistic act? *(implicit — group-by, not stored)* | computed over `deliveries` |
| **Delivery** | Which file did we actually fetch? | `deliveries` table row, keyed by `slug` |

A "recording" — same reciter performing the same style+riwayah+year — is **not** a stored entity. It's a group-by:

```
recording = (reciter_id, riwayah, style, recording_year, variant_label)
```

Sibling deliveries of the same recording (e.g. mp3quran + qul both serving Mishary's 2008 Hafs Murattal) are discovered with `GROUP BY` over `deliveries`. No `recording_id` middle layer — denormalizing year/variant across CDN-rows is cheap and removes a foreign key.

### Source × Channel are orthogonal

- **`source`** = the website we found the audio on (mp3quran.net, everyayah.com, qul.tarteel.ai, surahquran.com, quranicaudio.com, tvquran.com).
- **`channel`** = the host actually serving the bytes (`server*.mp3quran.net`, `download.quranicaudio.com`, `audio-cdn.tarteel.ai`, `download.tvquran.com`, `everyayah.com`, `*.archive.org`).

Relationship is M:N. One source can route through multiple channels (qul → both `tarteel` and `quranicaudio`); one channel can serve multiple sources (`quranicaudio` channel serves both `quranicaudio` and `qul`; `archive_org` serves `surah-quran`).

The `(source, channel)` pair on each delivery row is **authoritative** — vocab stores neither direction of the mapping. The materialized rollup `derived.source_channels[]` caches it (refreshed by `repo_catalog.refresh_derived()` on every delivery add).

**CDN is the primary dedup axis.** Same `(reciter, riwayah, style, channel)` is kept as separate deliveries when bitrate, sample rate, channels, or duration differ — different masters. Within-tuple disambiguators on the slug: `_<bitrate>k` when bitrates differ, `_v2` otherwise (§3).

### Download-only sources (YouTube / yt-dlp)

Some audio isn't a CDN of direct `.mp3` URLs — it's a **playlist** on YouTube (or any yt-dlp-supported host: SoundCloud, archive.org, Spreaker). These map onto the same model:

- **`channel`** = the host (e.g. `youtube`). Surfaces in the public dashboard filters like any channel. The intake plan proposes it by matching the source URL against the catalog channels' `host_patterns` (`services/admin/intake_plan/identity.py::channel_for`); the channel must already exist in vocab — the online mint adds sources, not channels (channel vocab is data, not schema; §10).
- **`source`** = the *uploader* (per-uploader provenance, e.g. a specific YouTube channel). **Not** surfaced in public filters (there is no `source` filter axis — only `channel`). Proposed by the plan: an existing generic source for Drive / SoundCloud / archive.org (`google_drive` / `soundcloud` / `archive_org`), else for YouTube a new `<uploader>_youtube` source built from the playlist's channel name + URL. The owner edits it in the Requests-tab identity form; minting adds a missing source via `vocab_additions` (idempotent).
- **`source_url`** = the originating playlist / folder URL, stored on the delivery row (§4); the reciter-detail modal renders the channel cell as a hyperlink to it. Per chapter, a file holding several chapters is kept as the manifest chapter's `source_url` while its `url` is the bucket chapter mp3.

Download-only deliveries are **bucket-served only**: their per-chapter `audio/<ch>.mp3` is created by the align pipeline's acquire (and, for combined files, split) HF job (YouTube serves opus/m4a, never mp3 — see §5). The watch-page URL in the sidecar is provenance, not a streamable fallback — the audio-proxy never streams it; playback always uses the persisted bucket mp3.

## 3. Slug convention

```
<reciter_id>[_<riwayah_short>][_<style_short>][_<year>]_<channel_short>[_<disambiguator>]
```

Fixed ordering (left to right): reciter, riwayah, style, year, channel, disambiguator.

**Regex:** `SLUG_RE` = `^[a-z][a-z0-9_]{1,79}$` (`qua_shared/schemas/config/state.py`). ASCII lowercase, single underscores. No code parses the slug — opaque human-readable ID. `reciter_id` and delivery `slug` both use this regex (`ReciterEntry`, `Delivery` validators). Source slug is the exception: `SOURCE_SLUG_RE` = `^[a-z][a-z0-9_-]{1,79}$` allows hyphens (`surah-quran`).

**Suffix rules:**

| Component | Omit when | Source |
|---|---|---|
| `riwayah_short` | `hafs` (Hafs an Asim, the default) | `riwayahs.short` |
| `style_short` | `murattal` (the default) | `styles.short` |
| `year` | not known — never invent | row's `recording_year` |
| `channel_short` | **never** — mandatory | `channels.short` |
| `disambiguator` | only when same `(reciter, riwayah, style, year, channel)` has >1 delivery | derived at seed |

**Disambiguator forms:**
- `_<bitrate>k` — colliding deliveries differ by bitrate, e.g. `mahmoud_khalil_al_husary_qdc_64k` vs `..._qdc_128k`.
- `_v2`, `_v3`, … — fallback when bitrates match (different masters, same encoding); extend the chain when 3+ collide.
- `_byayah` — appended **after** the channel for a `by_ayah` delivery: `ahmad_al_nufais_tarteel_byayah`. Category marker, not a within-tuple disambiguator; stays last so left-to-right ordering holds for everything before it.

**Worked examples:**

| Source manifest | Slug |
|---|---|
| `mp3quran/abdulbasit_abdulsamad.json` | `abdulbasit_abdulsamad_mp3quran` |
| `qul/abdulbasit_abdulsamad.json` (→ tarteel) | `abdulbasit_abdulsamad_tarteel` |
| `qul/abdulbasit_abdulsamad_qdc.json` | `abdulbasit_abdulsamad_qdc` |
| `qul/abdulbasit_abdulsamad_mujawwad_qdc.json` | `abdulbasit_abdulsamad_mujawwad_qdc` |
| `mp3quran/abdelaziz_sheim_warsh.json` | `abdelaziz_sheim_warsh_mp3quran` |
| `surah-quran/abbadi_houssem_eddine.json` (→ archive.org) | `abbadi_houssem_eddine_archive` |
| `by_ayah/qul/ahmad_al_nufais.json` (tarteel, by_surah sibling exists) | `ahmad_al_nufais_tarteel_byayah` |

Renames are free for a bare catalog row — URLs are preserved per-delivery in the sidecar; nothing about the audio depends on the slug. **But once a lifecycle state row exists** (`delivery_states`/`transitions`, `reciters/<slug>/`, the audio manifest sidecar all key on the slug), a rename becomes a real cross-table migration. This is why slug minting for admin-dashboard **intake** contributions is deferred to the online intake plan's mint (`services/admin/intake_plan/`), not taken at submit: the slug's mandatory `channel_short` suffix needs the channel, which is only known once the source is enumerated. The plan's `identity.py` proposes `<reciter_id>[_<riwayah>][_<style>][_<year>]_<channel_short>` with a `_v2`… disambiguator on collision, the owner reviews it, and minting gets it right on the first try rather than guessing at submit and migrating later.

## 4. Schema

> **Authority**: the pydantic models at `qua_shared/schemas/bucket/catalog.py` are the runtime authority (cross-consumer: Inspector, training pipeline, dataset builder, GH Actions). FE types are codegen'd from them — never hand-edit `inspector/frontend/src/lib/types/generated/schemas.ts`. The tables below map pydantic field ↔ SQLite column.

> **Null convention**: any `<type> | null` field accepts `null` = "missing / not yet identified". Never `""` or `"unknown"` sentinels. Applies to `name_ar`, `country`, `recording_context`, `recording_year`, `variant_label`, and all audio-metadata fields.

DDL: `inspector/services/db/migrations/0001_init.sql`. Datetimes are TEXT ISO-8601 UTC; JSON-valued columns (`audio_categories`, `host_patterns`, `derived`) are TEXT (orjson). FKs enforced by SQLite; uniqueness + cross-table FK integrity also re-validated by `ReciterCatalog`'s `_check_unique_keys` model validator on snapshot.

### Vocab — `riwayahs` / `styles` (`_ShortNamed`)

| Column | Type | Notes |
|---|---|---|
| `slug` | TEXT PK | e.g. `hafs_an_asim`. Immutable. |
| `short` | TEXT | Used in delivery slugs, e.g. `hafs`. |
| `name` | TEXT | Display, e.g. `Hafs A'n Assem`. |

`styles` values: `murattal` / `mujawwad` / `muallim` / `children_repeat` / `hadr`. Legacy `taraweeh` style was migrated to `style: "murattal" + recording_context: "taraweeh"` — the `taraweeh` style slug is gone from vocab. `hadr` = fast-paced, less ornamented than murattal.

### Vocab — `sources` (`Source`)

| Column | Type | Notes |
|---|---|---|
| `slug` | TEXT PK | `mp3quran`, `qul`, `surah-quran` (hyphen allowed in source slug only). |
| `name` | TEXT | Display. |
| `url` | TEXT \| null | Website root. |
| `audio_categories` | TEXT (JSON `string[]`) | Subset of `["by_surah", "by_ayah"]` (`AudioCategory` enum). |

### Vocab — `channels` (`Channel`)

| Column | Type | Notes |
|---|---|---|
| `slug` | TEXT PK | `mp3quran`, `quranicaudio`, `archive_org`. |
| `short` | TEXT | Used in delivery slugs, e.g. `qdc` for `quranicaudio`, `archive` for `archive_org`. |
| `name` | TEXT | Display. |
| `host_patterns` | TEXT (JSON `string[]`) | Globs matched against URL hostnames at ingestion. |

### Vocab — `recording_contexts` (`RecordingContext`)

| Column | Type | Notes |
|---|---|---|
| `slug` | TEXT PK | `Literal["studio","broadcast","prayer","taraweeh","mixed"]`. |
| `name` | TEXT | Display. |

Orthogonal to `style` (pace/ornamentation). Semantics: `studio` = deliberate studio recording; `broadcast` = radio (iza'a)/TV; `prayer` = live non-Taraweeh prayer (Fajr, Qiyam, …); `taraweeh` = Ramadan night prayers (split from `prayer` for volume + annual cadence); `mixed` = single delivery spanning multiple contexts. Delivery's `recording_context` is **nullable** — `null` = not yet classified (default for seed rows). No `"unknown"` sentinel.

### `reciters` (`ReciterEntry`)

| Column | Type | Notes |
|---|---|---|
| `reciter_id` | TEXT PK | Slug-form (`SLUG_RE`), e.g. `mahmoud_khalil_al_husary`. Immutable. |
| `name_en` | TEXT (≥1) | Canonical display. §6 naming guide. |
| `name_ar` | TEXT \| null | Arabic; `null` if unknown. |
| `country` | TEXT \| null | ISO-2 or `null`. |
| `notes` | TEXT \| null | Free-form, ≤500 chars. |

Editable columns: `_RECITER_WRITABLE = (name_en, name_ar, country, notes)` (`repo_catalog.py`). `reciter_id` is immutable.

### `deliveries` (`Delivery`)

| Column | Type | Notes |
|---|---|---|
| `slug` | TEXT PK | Per §3. |
| `reciter_id` | TEXT FK→`reciters` | |
| `riwayah` | TEXT FK→`riwayahs.slug` | |
| `style` | TEXT FK→`styles.slug` | Pace/ornamentation only. |
| `source` | TEXT FK→`sources.slug` | |
| `channel` | TEXT FK→`channels.slug` | |
| `source_url` | TEXT \| null | Originating playlist/set URL for download-only sources (YouTube playlist, SoundCloud set). `null` for CDN deliveries — their per-chapter URLs live in the sidecar. Surfaced on `PublicDelivery`; the reciter-detail channel cell hyperlinks to it. |
| `recording_context` | TEXT \| null FK→`recording_contexts.slug` | `null` when unclassified. Orthogonal to style. |
| `recording_year` | INTEGER \| null | 4-digit Hijri or CE; null when unknown. |
| `variant_label` | TEXT \| null | Free token (`madinah`, `studio_2008`); rare. |
| `audio_category` | TEXT | `by_surah` \| `by_ayah` (`AudioCategory`). |
| `chapter_count` | INTEGER (≥0) | URL count. Full mushaf = 114 (by_surah) or 6236 (by_ayah); partial coverage lower. |
| `codec` | TEXT | `mp3`. |
| `container` | TEXT | `mp3`. |
| `sample_rate_hz` | INTEGER \| null | Probed; null for unprobed by_ayah. |
| `channels` | INTEGER \| null | 1 (mono) or 2 (stereo); `ge=1, le=2`. |
| `bitrate_mode` | TEXT | `BitrateMode` enum (§5). Default `unknown`. |
| `bitrate_kbps_nominal` | INTEGER \| null | CBR exact / VBR avg / ABR target. **`null` when `bitrate_mode == "mixed"`** (model validator enforces). |
| `total_duration_sec` | INTEGER \| null | Whole-mushaf seconds. |
| `added_at` | TEXT (datetime) | ISO-8601 UTC. |
| `added_by_hf_id` | TEXT | HF user id of the maintainer who added the row. |

Editable columns: `_DELIVERY_WRITABLE` (`repo_catalog.py`) covers riwayah/style/recording_context/recording_year/variant_label/source/channel/source_url/audio_category/chapter_count/codec/container/sample_rate_hz/channels/bitrate_mode/bitrate_kbps_nominal/total_duration_sec. The capability-gated `PATCH /api/admin/catalog/delivery/<slug>` surface exposes that complete metadata set; `PATCH /api/admin/catalog/reciter/<reciter_id>` exposes name, Arabic name, country, and notes. `slug`/`reciter_id` and provenance fields (`added_at`, `added_by_hf_id`) are immutable. Explicit `null` clears nullable metadata. Every edit is audited; public-projection edits mark the affected HF/GH release rows `catalog_edit`-stale. Checksum does **not** live on the row (sidecar `_meta.checksum` only).

### Inspector visibility and EveryAyah

`everyayah` is retained in SQLite for owner operations, but it is an internal
Inspector-only channel. Non-owner catalog projections remove its deliveries,
remove reciters that have no remaining delivery, and recompute derived channel
counts from the visible set. The same rule applies to dashboard lists, filters,
stats, activity, review/release status, audio/timestamp reads, and static catalog
data. Owners receive the canonical projection and may edit these rows; EveryAyah
is never eligible for a public HF dataset split or GitHub release member.

### `audio_manifest/<slug>.json` (bucket sidecar — `AudioManifestSidecar`)

```jsonc
{
  "schema_version": 1,
  "slug": "<delivery slug>",
  "_meta": {                                 // SidecarMeta (alias "_meta")
    "checksum": "sha256:<hex>",              // sole storage; row carries no copy
    "source_meta_reciter": "<original>",     // migration/audit
    "source_manifest_path": "data/...",      // original ingestion path
    "chapter_count": 114,
    "category": "by_surah"
  },
  "chapters": {                              // dict[str, ChapterEntry]
    "1": { "url": "https://...", "size_bytes": 803456, "duration_sec": 50, "bitrate_kbps": 128, "bitrate_mode": "cbr", "max_linear_seek_err_ms": 26 }
  }
}
```

Chapter keys: `"1"`–`"114"` (by_surah) or `"<surah>:<ayah>"` (by_ayah). Per-chapter metric fields nullable until probed. `ChapterEntry` fields: `url` (required), `size_bytes`, `duration_sec`, `bitrate_kbps`, `bitrate_mode` (`cbr`/`vbr` per chapter), and `max_linear_seek_err_ms` (probe verdict evidence).

`sources` (`list[ManifestSource]`, `{url, title}`) holds playlist files whose surahs are not known yet: an online playlist intake mints with `chapters: {}` and every included file here. The align run detects each file's surahs, writes the chapters (bucket `url` + original `source_url` + `source_offset_ms`) and clears `sources`. The key is omitted from the dump when empty, so every other manifest keeps its on-disk shape.

`AudioManifestSidecar` (`qua_shared/schemas/bucket/catalog.py`) is a bucket artefact with pure `extra="forbid"`: any unknown/legacy sidecar field raises `ValidationError` on parse (writer-drift signal), never silently stripped — the same external-file strictness the bucket-validation harness surfaces.

**Checksum** (`_meta.checksum`): `sha256(normalized_urls_sorted.joined_by_newline)` at build time. Normalization: lowercase hostname, strip trailing slashes, drop fragment; query order + path case preserved (CDN-sensitive). Lives only in the sidecar; re-probe jobs compute + compare.

`audio_meta.py` reads sidecars via `get_backend().read_json(audio_manifest_path(slug))` and caches each into LRU slots (`_audio_manifest` doc + `_audio_manifest_url_index` URL→chapter inverse). Powers VBR routing (`is_vbr`), audio-proxy short-circuit (`chapter_for_url`), the `/peaks` route's URL enumeration (`chapter_urls`), and FE warmup hints (`vbr_chapters_for_reciter`, `chapter_bitrate_kbps_for_reciter`).

### `catalog_aliases` (`Alias`)

Empty in seed. Reserved for slug/reciter_id renames. Columns: `kind` (`slug`|`reciter_id`), `old`, `new`, `ts`. Insert via `repo_catalog.insert_alias`.

### `catalog_meta` + `derived` (`Derived`)

Single row `id=1`: `schema_version` (2), `generated_at`, `derived` (TEXT JSON). `derived.source_channels[]` = M:N rollup `{source, channel, delivery_count}` per observed pair, **persisted** verbatim (not reconstructed) for parity; recomputed by `repo_catalog.refresh_derived()` whenever the delivery set changes (deterministic order `(source, channel)`).

## 5. Audio metadata: row vs sidecar

| Field | Uniform across chapters? | Lives in |
|---|---|---|
| `codec`, `container`, `sample_rate_hz`, `channels`, `bitrate_mode` | yes (encoder settings) | `deliveries` row |
| `bitrate_kbps_nominal` (CBR exact; VBR avg) | yes (label) | row |
| `total_duration_sec` (whole mushaf) | yes (aggregate) | row |
| `bitrate_kbps` (measured per chapter) | no (varies for VBR) | sidecar |
| `duration_sec` / `size_bytes` / `url` (per chapter) | no | sidecar |

### CBR vs VBR

Row-level `bitrate_mode` (`BitrateMode` enum) derived from per-chapter probe data:

Enum collapsed to four values by migration 0014 (legacy `mostly_cbr`/`mostly_vbr`/`abr` folded into `mixed`):

| Value | Meaning |
|---|---|
| `cbr` | All probed chapters CBR. `bitrate_kbps_nominal` = exact. |
| `vbr` | All probed VBR. `bitrate_kbps_nominal` = target / observed average. |
| `mixed` | Chapters disagree (any CBR+VBR within the delivery, or differing CBR rates). Use sidecar for per-chapter truth. **`bitrate_kbps_nominal` is null** (model validator enforces). |
| `unknown` | No chapters probed yet (default for by_ayah). |

Per-chapter `ChapterEntry.bitrate_mode` (sidecar) is `cbr`/`vbr` only — the `mixed` collapse is a row-level rollup over chapters that individually are one or the other.

Detection — **whole-file linear byte→time seek error** (`qua_shared/mp3_frames.py::classify_bitrate_mode`, mirrored inline in `probe_audio_meta.py`): walk every audio frame, measure how far a browser's linear `time→byte` seek would land from each frame's true time; `≤ 200 ms` ⇒ `cbr`, else `vbr`. This is the only reliable signal — it is exactly what the playback transport needs to know (can the browser seek this natively?). The two shortcuts this replaced both produced systematic mislabels: mutagen's header `bitrate_mode` calls every Xing-tagged file VBR (incl. CBR audio our pipeline tagged Xing) and `len(set(bitrates))==1` / head-only uniformity is fooled by a stray frame or by VBR whose variation starts past the head (whole tvquran/archive reciters slipped through as CBR and stalled on seek). Because the metric needs every frame, source probing (`probe_audio_meta.classify`, `mp3_probe.probe_source(allow_full=True)`) downloads the full file. Per-chapter results land in the sidecar, rolled up to the row at build.

**Download-only sources create their own encode.** Unlike CDN audio (publisher mp3 bytes preserved verbatim, with only a Xing seek header injected via `-c:a copy`), a YouTube/yt-dlp or Drive source is opus/m4a/arbitrary — so the acquire job produces the canonical mp3 once: **192 kbps CBR, 44.1 kHz, source channel count** (or the delivery's `channels` override), cover-art stripped (`qua_jobs/audio_io.py::encode`). Because the watch URL can't be HTTP-frame-probed, the manifest is minted with URLs only and the per-chapter size / duration / source offset are written back from the produced files by the align pipeline (`services/admin/align_pipeline/manifest.py`). Forced-CBR ⇒ these deliveries never hit the VBR playback path.

### Style vs recording_context

- **`style`** = pace/ornamentation: `murattal` / `mujawwad` / `muallim` / `children_repeat` / `hadr`.
- **`recording_context`** = why the recording exists: `studio` / `broadcast` / `prayer` / `taraweeh` / `mixed`; `null` when not identified.

Two independent axes — "what kind of recitation, and where from?" The `taraweeh` style slug is removed from vocab; new taraweeh entries pick `style: "murattal"` + `recording_context: "taraweeh"`.

## 6. Naming style guide

New `name_en` entries must follow these (applied at seed).

| Axis | Rule |
|---|---|
| `Al-` prefix | `Al-<Surname>` (capital A, hyphen). Never `AlSurname`, `al-surname`, `Al Surname`. |
| Egyptian `El-` | **Preserve** when source-attested (Ahmed El-Shafei, Hamza El-Far). Regional signal. |
| Sun-letter (`Az-`, `Ath-`, `As-`) | Preserve where attested. Don't retroactively introduce — `Al-Sudais` stays. |
| `Abdul-` / `Abdel-` | **Glued**: `Abdulrahman`, `Abdelaziz`. Never spaced/hyphenated. |
| First-name canonical spellings | `Mohammed` (not Muhammad/Mohamed/Mohammad), `Ahmed` (not Ahmad/Ahmet), `Yusuf` (not Yousef/Youssef/Yousif), `Mahmoud` (not Mahmood). Also `Khalid`←Khaled, `Mansour`←Mansoor, `Mishary`←Mishari/Meshari, `Adel`←Adil, `Khalil`←Khaleel, `Yasser`←Yassir, `Mustafa`←Mostafa, `Hisham`←Hesham, `Saeed`←Sayeed, `Tawfiq`←Tawfeeq. Maintainer override: canonical normalization is deliberate (a prior report recommended against it to preserve regional signal — overridden). |
| `bin` patronymic | Lowercase between names: `Ahmed Talib bin Humaid`. |
| Honorifics | **Strip** (Sheikh, Sheik, Shaik, Qari, Hafiz). One seed exception (`Ustaz Zamri`). |
| Numbers in names | Only Madinah/Makkah Taraweeh series years (`1437H`). |
| Apostrophes | Straight ASCII `'` (U+0027). |
| Compound-word casing | Lowercase after first letter (`Abdulmajid`, never `AbdulMajid`). |
| Slug mirrors name | `Al-X` → `al_x` (e.g. `mahmoud_khalil_al_husary`). |

`name_ar` is **preserved as-source** — no Arabic normalization beyond emptying a literal `"unknown"` to `null`.

## 7. Seed origin (historical)

The seed catalog (864 deliveries, 422 reciter clusters, 0 slug collisions) was built one-time by a dedup + probe pipeline whose scratch artifacts lived in `.local/dedup/` (gitignored): dual-agent name clustering, same-channel ffprobe duplicate detection, naming-consistency + canonical-spelling normalization passes, then a bulk 256 KB-range audio probe and a `build_catalog.py` assembly to `reciter_catalog.json` + sidecars. That JSON was later migrated into SQLite (`scripts/migrations/migrate_json_to_sqlite.py`). The `.local/dedup/` artifacts are historical scratch — not load-bearing. Seed rows carry `added_at = 2026-05-12T00:00:00Z`, `added_by_hf_id = system_seed`.

## 8. Mutation surface

All catalog writes go through `inspector/services/state/catalog.py` (sole writer). Each mutation requires `_require_maintainer(actor)` (MAINTAINER or OWNER → else `NotAuthorizedForCatalog`), runs inside `db.sync.durable_transaction()` (atomic with its audit row; nesting-safe for the accept flow), and appends a `catalog.*` audit event.

| Service fn | Repo fn | Audit event | Notes |
|---|---|---|---|
| `add_reciter` | `repo_catalog.add_reciter` | `catalog.added` (kind=reciter) | `Duplicate` → `InvalidCatalogChange`. |
| `edit_reciter` | `repo_catalog.edit_reciter` | `catalog.edited` (kind=reciter) | Patch computed before txn; no-change → no I/O. `reciter_id` immutable. |
| `add_delivery` | `repo_catalog.add_delivery` | `catalog.added` (kind=delivery) | Refreshes `derived`. FK/`Duplicate` → `InvalidCatalogChange`. |
| `edit_delivery` | `repo_catalog.edit_delivery` | `catalog.edited` (kind=delivery) | Public surface = riwayah/style/recording_context/recording_year only. FK violation → `InvalidCatalogChange`. |
| `add_audio_source` | `repo_catalog.add_source` | `catalog.audio_source_added` | New vocab source. |

Audit `patch` shape `{field: {from, to}}` is computed in the service; the repo persists only. Reads: `snapshot()` (cached on `db_seq`), `find_delivery`, `find_reciter`, `display_name`.

**Public-projection edits stamp release staleness.** `edit_delivery` / `edit_reciter` carry `PUBLIC_DELIVERY_FIELDS` / `PUBLIC_RECITER_FIELDS` — the catalog fields that surface in the HF `mushafs` catalog + GH release `catalog.json`. When a changed field is in that set, the service stamps the affected published `hf`/`gh` rows `catalog_edit`-stale (`repo_releases.stamp_stale` / `stamp_stale_for_reciter`, reciter edits fanning out to every delivery) inside the same txn — so the Releases tab surfaces the drift and offers the cheap catalog refresh. Admin-only fields (`notes` / `variant_label` / `source`) never stamp. → [`dataset-and-releases.md`](dataset-and-releases.md).

## 9. Adding a single reciter / delivery

1. **Check for collision.** `find_reciter(reciter_id)` and scan `name_en`/`name_ar` variants. If the reciter exists, you're adding a *delivery* — skip to step 3.
2. **Add the reciter** via `catalog.add_reciter(actor=…, reciter_id=…, name_en=…, …)`. Pick `reciter_id` per `SLUG_RE` + §6.
3. **Decide the delivery shape** — riwayah, style, recording_year (if known), source (must exist in `sources`; `add_audio_source` first if not), channel (must exist in `channels`; §10 to add).
4. **Compute the slug** (§3); apply disambiguator rules on collision.
5. **Probe** at least chapter 1 (`scripts/audio/probe_audio_meta.py`) → `bitrate_mode`, `bitrate_kbps_nominal`, `sample_rate_hz`, `channels`. `codec`/`container` = `mp3`.
6. **Add the delivery** via `catalog.add_delivery(actor=…, delivery=Delivery(...))` (constructs + validates the pydantic model; FK + duplicate enforced at insert; `derived` auto-refreshed; row + audit committed durably to the bucket DB).
7. **Generate the sidecar** at `catalog/audio_manifest/<slug>.json` (URL map; per-chapter metrics may be null until a later probe). Checksum → `_meta.checksum`, never the row.

There is no separate validate-then-rebuild step: the SQLite FKs + `Delivery`/`ReciterCatalog` validators enforce integrity at write time, and `sync` uploads the DB after the committed txn.

## 10. Adding a source / channel

**New source** (`vocab.sources`): `catalog.add_audio_source(actor=…, source=Source(slug=…, name=…, url=…, audio_categories=[…]))`. Hyphens permitted in source slug only.

**New channel** (`vocab.channels`) — needed when bytes come from an unregistered host. No service endpoint today; insert via `repo_catalog.load_vocab` / a migration:
1. Identify the host pattern — most specific glob without false positives (`download.example.com`, not `*.example.com`).
2. Slug: lowercase ASCII + underscores, 2–40 chars, `<domain_root>` or `<host>_<role>` (`archive_org`).
3. `short`: ≤10 chars, mnemonic, used in delivery slugs.
4. Verify the probe's host→channel mapping picks up the new pattern.

**Channel host migration** (CDN relocates, e.g. `download.tvquran.com` → `cdn.tvquran.net`): add the new pattern to `channels.host_patterns` alongside the old (keep old for historical sidecars); re-scrape affected manifests (row identity stable, sidecar URLs change → `_meta.checksum` changes, invalidating downstream caches); re-probe; audit-log the date + host shift + delivery count.

### Registering a download-only (yt-dlp) source

YouTube, Google Drive, SoundCloud, archive.org and other yt-dlp hosts all flow through the **online intake** in the Requests tab: plan (enumerate + identity; surahs detected from the audio when aligning) → mint → align ([align-pipeline.md § Online intake](align-pipeline.md#online-intake-plan--mint--align)). Registering one is **data, not code**:

1. The catalog needs a `Channel` whose `host_patterns` match the host, with `gh_release_eligible=False` (not a public CDN — its source URLs can't go in a public GH release; the bucket mp3 is the distributed artifact). If none matches, the plan's identity comes back with no channel and the check blocks the mint until the owner picks one.
2. The per-uploader `source` is proposed by the plan (a new `<uploader>_youtube` source for a YouTube channel, else the host's generic source) and edited in the identity form; minting adds a new one via `vocab_additions` (a `Source` with `name`/`url`/`audio_categories`).

No new download/encode code is needed: enumeration is `services/admin/intake_plan/enumerate.py` (yt-dlp flat listing; Drive via `drive.py`), fetch + canonical encode (§5) is `qua_jobs/audio_io.py`, and the post-acquire manifest write-back is source-agnostic. YouTube downloads need the `INSPECTOR_YTDLP_COOKIES` Space secret ([config-deploy.md](config-deploy.md)).

## 11. Removing a delivery / reciter

Maintainer-only, manual; mandatory audit. No automation / row-level archive flag yet.

**Single delivery:** delete the `deliveries` row + the sidecar; `refresh_derived()`; if the reciter has no remaining deliveries optionally remove the `reciters` row (orphans are harmless); audit the reason + actor.

**Entire reciter:** remove all deliveries + sidecars, then the `reciters` row; optionally record a retirement alias; same audit requirement.

**Never repurpose a `reciter_id` or `slug`** — once retired it stays burned, repurposing breaks URL stability for any external system that cached the old mapping.

## 12. Probing

| Job | Script | Scope |
|---|---|---|
| Single-reciter VBR/CBR + bitrate | `scripts/audio/probe_audio_meta.py --reciter <slug>` | one manifest |
| Bulk row fields | `scripts/audio/probe_audio_meta.py --all` | all tracked reciters |
| Per-ayah probing | not run — `by_ayah` deferred until a consumer needs per-ayah metrics | — |

**How:** 256 KB HTTP Range fetch per URL (no full downloads). `mutagen` reads Xing/Info/VBRI/LAME (authoritative bitrate mode + duration); frame-by-frame scan as fallback. `Content-Range` response header gives total size free. URL-keyed probe cache short-circuits reruns. Concurrency capped (≤10 for `*.archive.org` politeness). Re-probe compares `_meta.checksum`; unchanged → skip.

**When:** new delivery (probe ≥chapter 1 for row fields); new source (bulk by_surah); URL-list change (= checksum change → re-probe that sidecar); periodic maintenance pass for silent format/URL drift.

## 13. Known limitations

- **`by_ayah` deliveries are unprobed** — row audio fields `null`, URL-only sidecars. Bounded re-probe work, deferred until a consumer needs per-ayah metrics.
- **Per-chapter `duration_sec` is null** when no Xing TOC is in the first 256 KB. Full-file probing deferred.
- **`surah-quran` source slug carries a hyphen** — tolerated in `sources.slug` only; never in `reciter_id`, delivery `slug`, or channel slug.
- **No aliases yet** — `catalog_aliases` empty; renames are manual.
- **No `recording_year` in the seed** — field exists; populating it is per-reciter research, deferred.
- **`recording_context` null for ~all seed rows** except migrated taraweeh. Backfill is a domain-research task, deferred.
