# Multi-riwayah in Universal Audio — finalized implementation plan

Status: **planned, not started.** Supersedes the surface audit that previously lived at this
path. Corrections against that audit are marked WAS.

Upstream contract landed in [QUA PR #99](https://github.com/Hetchy/qua/pull/99)
(`qua@main` = `c334291`) and [aligner app PR #55](https://github.com/Hetchy/qua-aligner-app/pull/55).
The aligner app is the **reference implementation** — mirror it, do not reinvent.

| Contract | In `qua` |
|---|---|
| Edition identity, scripts, fonts, projection | `docs/reference/editions.md` |
| Proxy timing contract + exception boundaries | `docs/plans/multi-riwayah-timing.md` |
| Deployment evidence, prod checklist | `docs/plans/multi-riwayah-deployment.md` |
| Source/coordinate audit | `docs/plans/multi-riwayah-source-audit.md` |

---

## 0. Decisions register

All settled. Do not re-litigate.

| # | Decision | Rationale |
|---|---|---|
| **D1** | The hardcoded coordinate tables (`MUQATTAAT_VERSES`, `STANDALONE_REFS`, `STANDALONE_WORDS`, `single_word_verses`) are **projected through `qua_domain`**, never duplicated per edition. WAS: projection is at **word granularity**, not verse granularity. | Owner decision. Word granularity is forced by the data: Warsh/Qalun merge Hafs `2:1`+`2:2` into Warsh `2:1` (8 words), so `(surah, ayah)` membership would suppress `boundary_adj` across a whole merged verse and over-flag `muqattaat`. Word granularity is **provably identity for Hafs** (every Hafs muqattaat verse has exactly one word). |
| **D2** | Shard format: `schema_version: 14` + `profile: "word" \| "native"` discriminator. Existing Hafs v13 shards are **not restamped**; absent `profile` reads as `native`; native meta accepts `Literal[13, 14]`. | Owner decision. One path, one LRU, one route, one release loader. 37 reciters x 114 chapters stay untouched. |
| **D3** | `basmala_amin`: the sounded-Basmala sub-check is emitted **only when the edition numbers the Basmala as `1:1`**, resolved via `load_edition_projection(riw).relation_for_source("1:1:1").kind != "opening_basmala"`. The Amin check and the missed-Basmala augmentation stay for **all** riwayat; the Amin check resolves the edition's last Fatiha verse via `get_surah(1, riw).ayah_count`. | Owner decision. Verified: `kind == "mapped"` for hafs/shuba, `"opening_basmala"` for warsh/qalun. All four editions currently have `ayah_count == 7` for surah 1 — derive it anyway, never hardcode. |
| **D4** | Verse word counts and per-word info go through the edition index. WAS: **Hafs keeps reading `data/surah_info.json` at runtime**, guarded by a parity test against `load_edition_index("hafs")`. | Owner decision. Verified: the two sources are already identical — 6,236 verses, 77,433 words, zero diffs. Keeping the Hafs runtime path on `surah_info.json` means the Hafs hot path is byte-identical and works with `qua_domain` absent; the parity test makes them one source of truth in practice. |
| **D5** | Hafs display text/font stays on `data/qpc_hafs.json` + `data/digital_khatt_v2_script.json` + `DigitalKhattV2.otf` (byte-identical to the aligner app). Only `warsh`/`qalun`/`shuba` take text **and** font from `qua_domain`. | Owner decision. `qua_domain`'s hafs index has the same 77,433 refs but different glyph spellings in 44,481 words (U+0652 vs U+06E1 and friends) — swapping would silently change every existing Hafs render, snapshot, and qalqala derivation. |
| **D6** | The Inspector takes a **real pip dependency on `qua-domain`** installed from the pinned `Hetchy/qua` Git commit, exactly like `qua-aligner-app/src/core/install_sdk.py`. **No bucket export.** WAS: `qua-sdk` is **not** installed. | Owner decision + DRY. `qua-domain` is `dependencies = []`, pure stdlib, `requires-python >=3.11`, ~12 MB. `qua-sdk` needs a Cython DP extension + numpy + a build toolchain on alpine, and its two projection entry points (`project_alignment`, `refresh_target_coverage`) take an SDK `Alignment` object the Inspector never holds — they belong to the offline Katana pipeline. |
| **D7** | Every coordinate the Inspector shows or edits is the **delivery's own edition coordinate**. Words render in the edition font. The ayah marker is the edition's own marker shape. | Owner decision. Mirrors the aligner: `verse_marker_prefix()` returns U+06DD for Hafs (Digital Khatt needs it) and `""` for the other three (the paired QPC fonts decorate the Arabic-Indic digits directly). |
| **D8** | WAS: **`@quranic-phonemizer/cells` is NOT changed and NOT re-pinned.** Word-profile rows render in a new Inspector-owned `WordTimedRow.svelte`. | The package renders the *native schema-2 cell wire*; a proxy-timed word row has no cells, sounds, columns, groups, or rule occurrences, and `parse()`/`parseCompact()` throw on a degenerate wire. Per-edition font is already a host `--qc-arabic`/`--qc-connected` token override (`styles/timestamps.css:30-31`); the ayah marker is drawn by the Inspector, not the package. See §12 for the release/re-pin mechanism, kept documented for the day a *native* non-Hafs phonemizer lands (that is a producer schema bump, hence a `cells` major). |
| **D10** | Teleprompter for non-Hafs is word-only animation, edition font, word-level highlight. **No per-edition shaped-glyph fixtures.** | Owner decision. `LineAnimation.svelte:84` already defaults to `EMPTY_SHAPED_GLYPHS` and falls through to the plain-text branch. Zero new rendering code. |
| **D11** | `auto_split_v1` is produced for non-Hafs. | Owner decision. Beam-30 MFA is wide enough to place a section cut; Auto Split already degrades to plain Split when `refs` is null. |
| **D12** | `low_confidence_v2.json` and `ts_validation.json` are **not produced** for non-Hafs. Registry rows stay; counts emit 0; accordions hide. | Owner decision. Both are tight-beam MFA probes against Hafs proxy phones — the signal degenerates to "this is not Hafs". |
| **D13** | Fix: the riwayah slug mismatch, `qua_jobs/publish_hf.py:633` dead code, and stale `docs/reference/shards.md`. WAS: also fix `docs/reference/validation.md` §"Bench / drift harness". | Owner decision + audit correction. See §10. |
| **D14** | Requests form: replace the "non-Hafs unsupported" callout with a supported-list callout. | Owner decision. |
| **D15** | The single source of truth for slug mapping is a new `qua_shared/riwayat.py` with an **explicit** `SUPPORTED_RIWAYAT` table, not a call to `normalize_riwayah`. `qua_domain`'s alias table is fixed upstream too (WP Q1), but the Inspector does not depend on that fix. | `normalize_riwayah("qalon_an_nafi")` and `normalize_riwayah("shubah_an_asim")` **raise `ValueError` today** (verified, see §3). An explicit table also works with `qua_domain` absent, which the Hafs-only degradation path needs. |
| **D16** | **No new capability** is registered for edition assets. They ride the existing ungated public static routes. | `/api/static/quran-refs.json` and `/api/ts/resource/<name>` have no capability gate today. Adding one would break the hardcoded anon list in `tests/services/test_capabilities.py`, plus `test_route_auth` and `test_dev_mode`, for zero security gain — the data is public reference text. |
| **D17** | The kill-switch is `INSPECTOR_MULTI_RIWAYAH` (default `1`; forced `0` when `qua_domain` is unimportable). A non-Hafs delivery with the switch off **fails loudly**, never silently renders Hafs. | An HF Space *variable* change + restart is a 60-second rollback; a Docker rebuild is 10+ minutes. Silent Hafs fallback on a Warsh delivery would corrupt reviewer edits with wrong coordinates. |
| **D18** | `qua-domain` is installed at **Docker build time** via a `--mount=type=secret` build stage, not at boot like the aligner app. | The Inspector has a Dockerfile (the aligner is a boot-installing Gradio Space). Build-time install means no cold-start latency, no runtime PAT in the container, and a self-contained image. The `CELLS_DEPLOY_KEY` mount at `inspector/Dockerfile:31-40` is the exact existing precedent. |
| **D19** | `low_confidence` thresholds ship with **all four editions equal to the Hafs value**, re-tuned from the first real non-Hafs delivery's score histogram. | No non-Hafs recitation exists yet; any other number is an unverifiable guess baked into a constant. |
| **D20** | `RELEASE_FORMAT_MAJOR` -> v4.0.0 is cut **only when the first non-Hafs reciter is actually publishable**, not with the schema work. | Hafs consumers should not be forced through a major version for a format they never see. |

---

## 1. What the Inspector calls from `qua_domain`

One accessor module owns the entire dependency: **`inspector/services/reference/editions.py`**
(Flask-free, per the `services-flask-free` CI guard). Nothing else in the tree imports `qua_domain`.

| `qua_domain` symbol | Inspector wrapper | Called from | Caching |
|---|---|---|---|
| `normalize_riwayah` | not used directly — `qua_shared.riwayat.to_sdk_slug()` (D15) | everywhere | pure dict |
| `get_edition(riw) -> EditionMetadata` | `editions.metadata(riw)` | `/api/static/edition/*`, manifest `editions` block, font route | `@lru_cache(4)` |
| `load_edition_index(riw) -> QuranIndex` | `editions.word_map(riw)`, `editions.word_counts(riw)` | `quran_refs._build`, `data_loader.get_word_counts`, `dk_text_for_ref` | `cache.get/set_edition_word_map(riw)` + keyed `word_counts` |
| `load_edition_projection(out, ref)` | `editions.projection(riw)` | coordinate-table projection, `basmala_amin`, WBW reverse-projection | `cache.get/set_edition_projection(riw)` |
| `get_ayah_word_count(s, a, riw)` | via `editions.word_counts(riw)` | validation, save, `normalize_ref` | as above |
| `get_surah(n, riw)` | `editions.surah(n, riw)` | last-Fatiha-verse resolution (D3), cross-chapter traversal | via projection cache |
| `get_stop_sign_profile(riw)` | `editions.stop_signs(riw) -> set[str]` | replaces module-level `constants.STOP_SIGNS` in `data_loader.word_has_stop` | `@lru_cache(4)` |
| `special_text(name, riw)` | `editions.special(name, riw)` | Basmala/Isti'adha display text for special-token segs | `@lru_cache(16)` |
| `read_font_asset(riw)` + `font_asset(riw)` | `editions.font(riw) -> (bytes, EditionAsset)` | `/api/static/edition/<riw>/font.ttf` | `cache.get/set_edition_font(riw)` (LRU 4, ~3.2 MB total) |
| `SUPPORTED_RIWAYAT`, `editions()` | `editions.available()`, `editions.all()` | `/healthz`, kill-switch, admission guard | module constant |
| `qua_sdk.project_alignment` / `refresh_target_coverage` | WAS: **not called by the Inspector.** They take an SDK `Alignment` object; the Inspector holds `detailed.json`. They live in `qua_alignment_batch` (Katana) — WP Q2. | — | — |

**Layering rules.** `services/reference/editions.py` is Flask-free and reads nothing from the
bucket. All memoisation goes through getters/setters added to `services/storage/cache.py` (never a
bare `global`, per the repo convention). Every new keyed cache is keyed on the **SDK riwayah slug**
so one edition's payload can never be served for another.

### Graceful degradation (no `QUA_SDK_PAT`)

`editions.available()` is `False` when the import fails **or** `INSPECTOR_MULTI_RIWAYAH=0`. Then:

| Surface | Behaviour |
|---|---|
| `get_word_counts()`, `dk_text_for_ref()`, `/api/static/quran-refs.json` | Hafs path, unchanged bytes. A non-Hafs argument raises `EditionsUnavailable`. |
| `/api/static/edition/<riw>/*` | 503 for non-Hafs; Hafs redirects to the existing endpoints. |
| `/api/seg/data/<slug>` on a non-Hafs delivery | 503 `ErrorEnvelope(error="editions_unavailable")` — **never** a Hafs render. |
| TS manifest | Non-Hafs reciters are dropped from `reciters`. |
| `auto_detect` | Refuses to fire `reciter.alignment_completed` for a non-Hafs delivery; logs a warning. |
| `/healthz` | New field `editions: []`; `status: degraded` (503 in deployed mode) **only if a non-Hafs delivery exists in the catalog**. A pure-Hafs deployment with no PAT is fully healthy. |
| pytest | `@pytest.mark.skipif(not editions.available())` on edition tests; the Hafs-identity tests run either way. |

---

## 2. `qua-domain` install mechanism

### 2.1 The pinned commit

`qua_shared/qua_domain_pin.py` (new):

```python
QUA_REPO = "Hetchy/qua"
QUA_COMMIT = "<40-hex>"          # bumped by hand, reviewed like a lockfile
QUA_PACKAGE_PATH = "packages/quran-domain"
QUA_DIST_NAME = "qua-domain"
```

Imported by the installer script **and** asserted at boot against
`importlib.metadata.distribution("qua-domain")` plus
`qua_domain.projection_asset_info().sha256`, so a stale image is caught by `/healthz`
rather than by a wrong render.

### 2.2 Docker (build-time, secret-mounted)

New stage in `inspector/Dockerfile`, between the ffmpeg stage and the runtime stage. It mirrors
`qua-aligner-app/src/core/install_sdk.py`: the PAT stays in the child process env, never in a URL,
a file, or pip's `direct_url.json`. `required=false` so a fork build still succeeds and the runtime
boots Hafs-only.

```dockerfile
FROM python:3.11-alpine AS qua-domain-build
RUN apk add --no-cache git
ARG QUA_COMMIT
WORKDIR /build
RUN --mount=type=secret,id=QUA_SDK_PAT,required=false \
    if [ -s /run/secrets/QUA_SDK_PAT ]; then \
      printf '#!/bin/sh\ncase "$1" in *sername*) echo x-access-token;; *) cat /run/secrets/QUA_SDK_PAT;; esac\n' > /askpass && \
      chmod 700 /askpass && \
      GIT_ASKPASS=/askpass GIT_TERMINAL_PROMPT=0 GIT_LFS_SKIP_SMUDGE=1 \
        git clone --filter=blob:none --no-checkout --depth 1 \
          https://github.com/Hetchy/qua.git qua && \
      git -C qua sparse-checkout set --no-cone packages/quran-domain && \
      GIT_ASKPASS=/askpass git -C qua fetch --depth 1 origin "$QUA_COMMIT" && \
      git -C qua checkout --quiet --detach "$QUA_COMMIT" && \
      [ "$(git -C qua rev-parse HEAD)" = "$QUA_COMMIT" ] && \
      pip wheel --no-deps --no-cache-dir -w /wheels qua/packages/quran-domain && \
      rm -f /askpass && rm -rf qua; \
    else \
      mkdir -p /wheels; echo "[qua-domain] no PAT - building Hafs-only image"; \
    fi
RUN mkdir -p /wheels
```

Runtime stage, appended to the existing `pip install` layer **before**
`pip uninstall -y pip setuptools wheel`:

```dockerfile
COPY --from=qua-domain-build /wheels /tmp/wheels
RUN pip install --no-cache-dir -r ./inspector/requirements.txt && \
    ( ls /tmp/wheels/*.whl >/dev/null 2>&1 && \
      pip install --no-cache-dir --no-deps --no-index --find-links=/tmp/wheels qua-domain \
      || echo "[qua-domain] absent - Hafs-only runtime" ) && \
    rm -rf /tmp/wheels && \
    pip uninstall -y pip setuptools wheel && \
    find /usr/local/lib/python3.11 -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
```

HF Spaces does not pass build args, so WAS: **the Dockerfile literal is the pin on the Space**.
`upload_inspector._stage()` rewrites the `ARG QUA_COMMIT=` default from `qua_shared/qua_domain_pin.py`
at stage time (one `re.sub`, alongside the existing Dockerfile overlay), and a unit test asserts the
two agree.

`inspector/requirements.txt` gains a comment block only — **no `qua-domain` line** (pip cannot
resolve a private Git dep without the PAT, and adding it would break `setup.sh` for contributors).

### 2.3 Secret plumbing

`QUA_SDK_PAT` = a fine-grained GitHub PAT with `Contents: read` on `Hetchy/qua`.
Provisioning mirrors `CELLS_DEPLOY_KEY`:

| Where | Change |
|---|---|
| GitHub repo secret | new `QUA_SDK_PAT` (manual) |
| `inspector-checks.yml` `backend-checks` | `env: QUA_SDK_PAT` + a best-effort `install_qua_domain.py` step before pytest |
| `inspector-deploy.yml` `deploy` | pass `QUA_SDK_PAT` into `upload_inspector.py`'s env |
| `docker-publish.yml` | `--secret id=QUA_SDK_PAT,env=QUA_SDK_PAT` |
| `scripts/deploy/upload_inspector.py` | second `add_space_secret` after the `CELLS_DEPLOY_KEY` one; **warn, do not fail** when absent (unlike `CELLS_DEPLOY_KEY`, which stays hard-required) |
| `scripts/deploy/smoke_boot.py` | optional `--secret` pass-through |
| `scripts/deploy/deploy_space.py` | same optional `add_space_secret` for contributor Spaces |
| dev + prod HF Spaces | `QUA_SDK_PAT` secret (manual) |

The PAT never reaches a running container: it is a build-secret mount, and the wheel carries no
credential (we `pip wheel` a local path, not a Git URL).

### 2.4 Local dev

New `scripts/devenv/install_qua_domain.py` — the Inspector's port of the aligner's
`install_sdk.py`, reduced to one package, same receipt file, same env-only askpass, no
native-extension verification (there is none). `setup.sh backend` calls it best-effort and prints a
clear note when the PAT is unset: Hafs works fully, non-Hafs surfaces 503.
`launch.py --mode fixtures` is unaffected — the seeded fixtures are Hafs-only.

### 2.5 Image size

| Component | Bytes |
|---|---|
| `generated/quran_domain.json` (Hafs reference index) | 5,945,085 |
| 4 x `<riw>.words.json.gz` | 2,224,109 |
| 4 x `<riw>.ttf` | 3,266,884 |
| `projection.json{,.gz}` + receipt | 103,840 |
| **installed total** | **~11.5 MB** |

For scale the image already ships `data/digital_khatt_v2_script.json` (9.98 MB) +
`data/qpc_hafs.json.gz` (1.45 MB) + `surah_info.json` (0.4 MB), on top of
`numpy`/`datasets`/`pyarrow`. Relative growth is low single-digit percent. Build time grows by one
shallow sparse clone plus a hatchling wheel build (~15 s).

---

## 3. Riwayah slug SSOT

WAS: **Blocker, verified.** `qua_domain.normalize_riwayah` rejects two of the four Inspector slugs:

```
hafs_an_asim       -> hafs
warsh_an_nafi      -> warsh
qalon_an_nafi      -> ValueError: unsupported riwayah: qalon_an_nafi
shubah_an_asim     -> ValueError: unsupported riwayah: shubah_an_asim
qalon              -> ValueError: unsupported riwayah: qalon
shubah             -> shuba
```

(`_ALIASES` at `packages/quran-domain/src/qua_domain/editions.py:21-35` has
`qaloonannafi`/`qalunannafi` but not `qalonannafi`, and `shubah` but not `shubahanasim`.)

**Fix, both sides.**

*Upstream (WP Q1)*: add `qalonannafi`, `qalon`, `shubahanasim` to `_ALIASES`, plus a test that
every Inspector vocabulary slug normalises.

*Inspector (WP P0)*: new `qua_shared/riwayat.py` — the SSOT, independent of `qua_domain`:

```python
SUPPORTED_RIWAYAT: dict[str, str] = {      # inspector slug -> SDK slug
    "hafs_an_asim":   "hafs",
    "warsh_an_nafi":  "warsh",
    "qalon_an_nafi":  "qalun",
    "shubah_an_asim": "shuba",
}
DEFAULT_RIWAYAH = "hafs_an_asim"
DEFAULT_SDK_RIWAYAH = "hafs"

def to_sdk_slug(inspector_slug: str) -> str: ...      # raises UnsupportedRiwayah
def from_sdk_slug(sdk_slug: str) -> str: ...
def is_supported(inspector_slug: str | None) -> bool: ...
def is_hafs(inspector_slug: str | None) -> bool: ...
```

Re-exported through `qua_shared/schemas/fe_types.py` as a small `RiwayatConfig` model so the
four-entry map lands in `generated/schemas.ts` — the two FE sites then compare against a generated
constant, never `short !== 'hafs'`.

A test asserts `SUPPORTED_RIWAYAT` keys are a subset of the `riwayahs` table's `slug` column, and
that `to_sdk_slug(k) == qua_domain.normalize_riwayah(k)` for all four (skipped when `qua_domain` is
absent).

---

## 4. The word-profile shard

### 4.1 On-disk shape

```jsonc
{
  "_meta": {
    "schema_version": 14,
    "profile": "word",
    "chapter": 40,
    "audio_category": "by_surah",
    "riwayah": "warsh",
    "edition_id": "warsh-v21+sdk-words-v1",
    "words_sha256": "...",
    "timing_provider": "hafs_proxy_mfa",
    "reference_riwayah": "hafs",
    "reference_id": "qul-text-qpc-hafs-312",
    "projection_id": "qua-edition-projection-v1",
    "projection_sha256": "..."
  },
  "readings": [{
    "id": "r1",
    "parts":      [["40:26", 1200, 5400, 0, 2]],
    "words":      [["40:26:13", "<exact target text>", 1200, 3100],
                   ["40:26:14", "<exact target text>", 3100, 5400]],
    "boundaries": [[1, null], [3, 26]]
  }]
}
```

- `parts` — **identical tuple shape to v13** `TsShardPart`: `(ref, start_ms, end_ms, first_word_index, word_count)`. Type reused verbatim.
- `words` — `(target_ref, exact_target_text, start_ms, end_ms)`. `target_ref` is the delivery-edition coordinate, or `0:0:N` for an unnumbered special (opening Basmala / Isti'adha ordinals — the timing contract's own convention).
- `boundaries` — one row per word: `(state_code, verse_end)`, `state_code` indexing `("start","join","sakt","stop")`, `verse_end` = the ayah number that ends here or `null`. Same fields v13 carries in `render.b[i][0]` and `render.b[i][4]`.
- **Absent by construction:** `phonemizer_version`, `native_schema_version`, `renderer_codec_version`, `render`, `timing.s`, `timing.a`, `timing.c`, `native_profile`.

Boundary *timing* is derived, never stored — the v13 rule (preceding word end to following word
start; first/last clamp to the part span; cross-reading stitch to the next reading's first word
start). WAS: that rule is phoneme-free and currently duplicated in
`qua_shared/timestamps_codec.py` (Python) and `compact-shards.ts` (TS); the word branch **calls the
same helpers**, it does not reimplement them.

### 4.2 Pydantic — `qua_shared/schemas/bucket/ts_shard.py`

```python
TS_SHARD_SCHEMA_VERSION = 14
TsShardProfile = Literal["native", "word"]

TsShardPart    = tuple[str, int, int, int, int]     # unchanged
TsWordRow      = tuple[str, str, int, int]          # ref, text, start_ms, end_ms
TsWordBoundary = tuple[int, int | None]             # state_code, verse_end


class TsNativeShardMeta(BaseModel):                 # was TsShardMeta
    model_config = ConfigDict(extra="allow")        # documented forward-compat exception
    schema_version: Literal[13, 14]                 # 13 = existing, never restamped
    profile: Literal["native"] = "native"
    chapter: int = Field(ge=1, le=114)
    audio_category: str = Field(min_length=1)
    phonemizer_version: str = Field(min_length=1)
    native_schema_version: Literal[2]
    renderer_codec_version: Literal[1]
    native_profile: TsNativeProfile


class TsWordShardMeta(BaseModel):
    model_config = ConfigDict(extra="allow")
    schema_version: Literal[14]
    profile: Literal["word"]
    chapter: int = Field(ge=1, le=114)
    audio_category: str = Field(min_length=1)
    riwayah: str = Field(min_length=1)              # SDK slug
    edition_id: str = Field(min_length=1)
    words_sha256: str = Field(min_length=64, max_length=64)
    timing_provider: Literal["hafs_proxy_mfa"]
    reference_riwayah: str = Field(min_length=1)
    reference_id: str = Field(min_length=1)
    projection_id: str | None = None
    projection_sha256: str | None = None


class TsWordShardReading(_Closed):
    id: str = Field(min_length=1)
    parts: list[TsShardPart]
    words: list[TsWordRow]
    boundaries: list[TsWordBoundary]

    @model_validator(mode="after")
    def _closure(self):
        if len(self.boundaries) != len(self.words):
            raise ValueError("word and boundary counts differ")
        for _, _, start, end in self.words:
            if end < start:
                raise ValueError("word timing end precedes start")
        for ref, start, end, first, count in self.parts:
            if not ref or end < start or first < 0 or count < 1:
                raise ValueError("invalid word part")
            if first + count > len(self.words):
                raise ValueError("word part references unknown words")
        return self


class TsWordShardDoc(_Closed):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    meta: TsWordShardMeta = Field(alias="_meta")
    readings: list[TsWordShardReading]


# Back-compat alias - existing import sites keep working unchanged.
TsShardMeta = TsNativeShardMeta
```

WAS: do **not** build a `Field(discriminator=...)` union at the `TsShardDoc` level — Pydantic
cannot discriminate on a *nested* field, and a top-level union would churn `generated/schemas.ts`
for every existing native consumer. Dispatch explicitly:

```python
# qua_shared/timestamps_shards.py
def shard_profile(doc: dict) -> str:
    return (doc.get("_meta") or {}).get("profile") or "native"   # absent => native (D2)

def parse_shard(doc: dict) -> TsShardDoc | TsWordShardDoc:
    return TsWordShardDoc.model_validate(doc) if shard_profile(doc) == "word" \
        else TsShardDoc.model_validate(doc)
```

`validated_brotli_shard` / `write_validated_shard` dispatch on `shard_profile`: native goes to
`audit_v13_document`, word to the new `qua_shared/timestamps_word_audit.py` (`audit_word_document`:
profile/version literal, dense word ids, part closure, monotonic non-overlapping intervals within a
reading, every `ref` valid in `load_edition_index(meta.riwayah)` **or** matching `^0:0:\d+$`,
`words_sha256` matches `get_edition(riwayah).words_sha256`, deterministic Brotli).

### 4.3 TypeScript — `lib/types/ts-client.ts`

```ts
export interface TsWordRow { ref: string; text: string; start_ms: number; end_ms: number; }

export interface TsWordShardReading {
    id: string;
    parts: TsShardPart[];
    words: TsWordRow[];
    timing: { words: TsWordTiming[]; boundaries: TsBoundaryTiming[] };
    states: Array<'start' | 'join' | 'sakt' | 'stop'>;
    verseEnds: Array<number | null>;
}

export interface TsNativeShardResponse { _meta: TsNativeShardMeta; readings: TsShardReading[]; }
export interface TsWordShardResponse   { _meta: TsWordShardMeta;   readings: TsWordShardReading[]; }
export type TsShardResponse = TsNativeShardResponse | TsWordShardResponse;

export const isWordShard = (s: TsShardResponse): s is TsWordShardResponse =>
    s._meta.profile === 'word';
```

`TsVerseData` gains `profile: 'native' | 'word'` and `riwayah: string`; its `native` field becomes
`readings: TsShardReading[] | TsWordShardReading[]`. `TsWord.letters` is `[]` and
`phoneme_indices` is `[]` for word profile (the shape does not change, so the waveform/animation
consumers need no branch).

### 4.4 Every reader that must learn the discriminator

**Python**

| File | Today | Change |
|---|---|---|
| `qua_shared/timestamps_codec.py` | `decode_reading`/`decode_document` assume `render` | add `decode_word_reading`/`decode_word_document`; extract the shared boundary-timing derivation |
| `qua_shared/timestamps_shards.py` | `validated_brotli_shard` -> `audit_v13_document` | dispatch on `shard_profile` |
| `qua_shared/timestamps_word_audit.py` | — | **new** |
| `qua_shared/timestamps_native.py` | reads `reading["analysis"]["result"]["words"]` + `timing["animation_tokens"]` | word branch: emit word rows with no `letters` array |
| `inspector/services/ts_reports/ts_target_snapshot.py:24` | `!= 13` returns `None` | accept `{13, 14}`; word docs resolve only `verse`/`word`/`boundary` kinds |
| `inspector/services/ts_reports/ts_target_snapshot.py:160,199` | hardcodes `13`, `native_schema_version: 2` | stamp the source shard's `schema_version` + `profile`; omit `native_schema_version` for word |
| `qua_shared/schemas/wire/ts_reports.py:184-185` | `native_schema_version: Literal[2]`, `shard_schema_version: Literal[12,13]` | `Literal[12,13,14]`; `native_schema_version: Literal[2] \| None`; new `shard_profile: TsShardProfile = "native"` |
| `qua_jobs/cut_release.py`, `qua_jobs/shard.py` | letter tier assumed | see §9 |
| `qua_shared/dataset_validation.py`, `qua_shared/coverage.py` | Hafs verse/word counts | edition-scoped counts |

**TypeScript**

| File | Change |
|---|---|
| `lib/recitation-data/compact-shards.ts:122` | `storedShard` hard-throws `schema_version !== 13`. Accept `13\|14`; branch on `_meta.profile ?? 'native'`; new `decodeWordShard()` reusing the existing part/boundary helpers |
| `lib/types/ts-client.ts` | §4.3 |
| `lib/recitation-data/native-shards.ts` | `assembleNative` gains a word branch: `words` straight from `TsWordRow`, `letters: []`, `phoneme_indices: []`, `intervals: []` |
| `lib/recitation-data/occasions.ts` | reads `reading.parts` only — verify, likely no change |
| `tabs/timestamps/utils/timed-entities.ts` | guard so word readings short-circuit to word entities only |
| `tabs/timestamps/components/TimedAnalysisRow.svelte` | dispatch to the new `WordTimedRow.svelte` when `profile === 'word'` |
| `tabs/timestamps/stores/report-mode.ts` | `ReportMode` union + `seedOwnFlags` restricted to `word`/`boundary` |

---

## 5. Coordinate projection — the concrete tables

Computed once per edition at first use, memoised in `services/storage/cache.py`.
New module `inspector/services/reference/edition_tables.py`:

```python
def muqattaat_words(riw: str)    -> frozenset[tuple[int, int, int]]
def standalone_refs(riw: str)    -> frozenset[tuple[int, int, int]]
def standalone_words(riw: str)   -> frozenset[str]      # bare skeletons
def single_word_verses(riw: str) -> frozenset[tuple[int, int]]
def fatiha_last_ayah(riw: str)   -> int
def basmala_is_numbered(riw: str) -> bool
```

Derivation: for each Hafs entry, `projection.project_range(<hafs word ref>)` gives the target word
refs (skeletons via `strip_quran_deco` on the target text). **Hafs derivation is the identity and
reproduces `inspector/constants.py` exactly** — that is the gate (§10).

Measured outputs, verified against the packaged assets:

| Table | hafs | shuba | warsh | qalun |
|---|---|---|---|---|
| `muqattaat_words` | 30 | 30 (identity) | **30** — Hafs `(42,1,1)`+`(42,2,1)` collapse into Warsh `42:1:1`+`42:1:2`, still two words | 30 |
| `muqattaat_verses` | 30 | 30 (identity) | **29** — the same merge, counted verse-wise | 29 |
| `standalone_refs` | 10 | 10 (identity) | 10, **4 shift**: `43:35:1`->`43:34:1`, `44:37:9`->`44:35:9`, `46:35:22`->`46:34:22`, `44:28:1`->`44:27:1` | same 4 |
| `standalone_words` (skeletons) | 8 | 8 (identity) | 8, **1 changes** — `وبٱليل` -> `وباليل` (confirmed) | same |
| — derivation | — | — | NOT a projection of `standalone_refs` (an unrelated *ref* allow-list): scan the 633 Hafs words whose skeleton is in the set, project each, take target spellings | same |
| `single_word_verses` | **28** | 28 (identity) | **3** (`55:63`, `89:1`, `93:1`) | 3 |
| Fatiha ayah count / word counts | 7 / `(4,4,2,3,4,3,9)` | 7 / same | 7 / `(4,2,3,4,3,4,5)` | 7 / same |
| `1:1:1` projection kind | `mapped` | `mapped` | `opening_basmala` | `opening_basmala` |

WAS: the `single_word_verses` collapse from 28 to 3 is *why* verse-granularity projection is wrong
(D1): in Warsh, `2:1` is an 8-word verse whose **first word** is the muqattaat.

### 5.1 Call-site rewrites (Hafs-identical by construction)

| Site | Today | After |
|---|---|---|
| `classifier.py:131` | `if (surah, s_ayah) in MUQATTAAT_VERSES` | `if (surah, s_ayah) in muqattaat_verses(riw)` — **CORRECTED during implementation.** The plan proposed narrowing this to `muqattaat_words`, on the premise that every Hafs muqattaat verse is one word long. Measured: false — 13:1 opens with the letters and runs on for eight more words, and `{word_counts[v] for v in MUQATTAAT_VERSES}` is `{1, 3, 4, 5, 6, 10, ...}`. Narrowing would newly flag one-word segments deep inside those verses across the 37 published Hafs reciters. The exemption stays verse-keyed; `edition_tables` therefore exposes **both** tables |
| `classifier.py:136` | `(surah, s_ayah, s_word) not in STANDALONE_REFS` | `... not in standalone_refs(riw)` |
| `classifier.py:138` | `strip_quran_deco(text) not in STANDALONE_WORDS` | `... not in standalone_words(riw)` |
| `classifier.py:312` | `s_word == 1 and (surah, s_ayah) in MUQATTAAT_VERSES` | `(surah, s_ayah, s_word) in muqattaat_words(riw)` — word-keyed is right *here*: Hafs-identical by construction, and Warsh's merged 42:1 carries a second opening at word 2 |
| `data_loader.get_single_word_verses()` | Hafs-derived singleton | `single_word_verses(riw)` |
| `routes/segments/data.py:68-71` (`/api/seg/config`) | global Hafs tables | `?riwayah=<slug>` query param, default `hafs_an_asim`; the default response is byte-identical (snapshot-pinned) |
| `data_loader.word_has_stop` -> `constants.STOP_SIGNS` | 4 fixed glyphs | `editions.stop_signs(riw)` — Warsh/Qalun have only U+06D6 (9,948 occurrences, semantics `optional_stop`); Hafs/Shuba keep the 6-sign inventory |

`constants.py` keeps the four literals as the **Hafs frozen baseline**, annotated as "identity
input to `edition_tables`; asserted equal to the derived Hafs table" — so `docs/reference/validation.md`
and the accordion guides stay readable.

### 5.2 `basmala_amin` rework (`detail.py:535`)

```python
# before
if surah == 1 and (s_ayah <= 1 <= e_ayah or s_ayah <= 7 <= e_ayah):

# after
last = fatiha_last_ayah(riw)                     # 7 for all four editions today
if surah == 1:
    if basmala_is_numbered(riw) and s_ayah <= 1 <= e_ayah:
        basmala_11.append(...)                   # sounded-Basmala sub-check (D3)
    if s_ayah <= last <= e_ayah:
        basmala_amin_17.append(...)              # Amin check - ALL riwayat
```

The missed-Basmala augmentation (`detail.py:573-598`) is unchanged — it is riwayah-agnostic (it
reads `pipeline_meta.deleted_basmala_chapters` and skips chapters 1 and 9).

### 5.3 `low_confidence` thresholds

`LOW_CONFIDENCE_THRESHOLD` / `LOW_CONFIDENCE_DETAIL_THRESHOLD` (`config.py:119,135`) become
`dict[str, float]` keyed on SDK slug, env-overridable per edition
(`INSPECTOR_LOW_CONF_WARSH=0.72`, ...). Per D19, ship all four equal to the Hafs value and re-tune
from the first real delivery's score histogram.

---

## 6. Ordered work packages

Each is independently committable. Repo is QUA (Inspector) unless marked **qua**.

### Q1 — riwayah aliases upstream *(qua)* — unblocks P1
- `packages/quran-domain/src/qua_domain/editions.py` `_ALIASES` += `qalonannafi`, `qalon`, `shubahanasim`.
- Test: every Inspector vocabulary slug normalises.
- Gate: qua domain suite.

### P0 — slug SSOT + doc corrections *(no behaviour, no dependency)*
- **new** `qua_shared/riwayat.py` (§3); re-export via `qua_shared/schemas/fe_types.py`; `python scripts/codegen/regen_fe_types.py`; commit `generated/schemas.ts`.
- `qua_jobs/publish_hf.py:626-633` — keep `_riwayah_for` but make the `detailed._meta.riwayah` branch live (paired with P4) and normalise via `from_sdk_slug`.
- `docs/reference/shards.md` — rewrite v12 to v13 throughout.
- `docs/reference/validation.md` §"Bench / drift harness" — replace with the real gates (§10).
- Gates: `schema-codegen-check`, `type-check`, backend pytest.
- Unblocks: P9, and every FE riwayah comparison.

### P1 — `qua-domain` install plumbing + accessor + kill-switch
- **new** `qua_shared/qua_domain_pin.py`; **new** `scripts/devenv/install_qua_domain.py`; **new** `inspector/services/reference/editions.py`.
- `inspector/Dockerfile` — `qua-domain-build` stage + runtime install (§2.2).
- `scripts/devenv/setup.sh` — best-effort backend step (§2.4).
- `scripts/deploy/upload_inspector.py` — second `add_space_secret`; `ARG QUA_COMMIT` rewrite in `_stage()`.
- `scripts/deploy/smoke_boot.py`, `scripts/deploy/deploy_space.py` — optional secret pass-through.
- `.github/workflows/{inspector-checks,inspector-deploy,docker-publish}.yml` — `QUA_SDK_PAT`.
- `inspector/config.py` — `MULTI_RIWAYAH_ENABLED`.
- `inspector/routes/auth/health.py` — `editions: list[str]` + the degraded rule (§1).
- `inspector/services/storage/cache.py` — new keyed getters/setters; `_word_counts` becomes keyed on SDK slug.
- `docs/reference/config-deploy.md` — `QUA_SDK_PAT`, `INSPECTOR_MULTI_RIWAYAH`, the new build stage.
- Gates: new `tests/services/test_editions_accessor.py` (available + unavailable paths); `smoke_boot.py --verify-boot` with and without the secret; `services-flask-free`; `type-check`.
- Unblocks: P2, P5, P6, P7.

### P2 — edition-scoped reference data
- `inspector/services/storage/data_loader.py` — `get_word_counts(riw)`, `get_single_word_verses(riw)`, `word_has_stop(..., riw)`; Hafs branch reads `surah_info.json` unchanged (D4).
- `inspector/services/reference/quran_refs.py` — `_build(riw)`, `build_payload(riw)`, `payload_hash(riw)`, `dk_text_for_ref(ref, riw)`; payload gains `riwayah` + `verse_marker_prefix`.
- **new** `inspector/services/reference/edition_tables.py` (§5).
- `inspector/routes/public/static.py` — `/api/static/edition/<riwayah>/refs.json` + `/version` + `/font.<ext>` (immutable, sha256 ETag). No capability gate (D16).
- `inspector/services/reference/timestamps.py` — manifest gains an `editions` block; `_RESOURCE_KEYS` unchanged; the `"hafs_an_asim"` fallback at line 183 becomes a hard skip + warning.
- `qua_shared/schemas/wire/timestamps.py` — `TsManifestResponse.editions: dict[str, TsEditionAsset] = {}`.
- `inspector/frontend/src/lib/refs/quran-refs.ts` — store becomes keyed by riwayah; sessionStorage key `quran-refs:<riwayah>:<hash>`.
- Gates: `test_hafs_tables_are_projection_identity`, `test_surah_info_matches_edition_index`, extended `test_quran_refs.py`, `seg_config.json` + `ts_manifest.json` snapshots unchanged.
- Unblocks: P5, P6, P7.

### P3 — word-profile shard schema + readers *(parallel with P1/P2)*
- `qua_shared/schemas/bucket/ts_shard.py` (§4.2); `__init__.py`, `fe_types.py`; regen FE types.
- `qua_shared/timestamps_shards.py` — `shard_profile`, `parse_shard`, dispatching `validated_brotli_shard`.
- **new** `qua_shared/timestamps_word_audit.py`.
- `qua_shared/timestamps_codec.py` — word branch + shared boundary derivation.
- FE: `compact-shards.ts`, `ts-client.ts`, `native-shards.ts`.
- `qua_shared/schemas/wire/ts_reports.py` + `inspector/services/ts_reports/ts_target_snapshot.py`.
- Gates: new `test_ts_shard_word_profile.py`, extended `test_ts_roundtrip.py` / `test_ts_shard_schema.py`, FE `compact-shards.test.ts` word branch, `schema-codegen-check`.
- Unblocks: P7, Q4, P10.

### P4 — `detailed.json` schema
- `qua_shared/schemas/bucket/segment.py`: `DetailedMeta.riwayah`, `DetailedSegment.source_ref`, `DetailedSegment.projection_support`.
- `qua_shared/schemas/bucket/pipeline_meta.py` — `riwayah`.
- `inspector/adapters/save_payload.py` + `inspector/services/segments/save.py` — carry both new fields (omit-when-`None`).
- Regen FE types.
- Gates: round-trip fixtures (a Hafs seg with neither field must serialise byte-identically); `schema-codegen-check`.
- Unblocks: Q2, P10.

### Q2 — offline extraction riwayah wiring *(qua)*
- `scripts/inputs_from_manifest.py` — carry the delivery riwayah (SDK slug) onto `inputs.json`.
- `qua_alignment_batch.job` — `align(..., riwayah=...)`; persist `matched_ref` = target, `source_ref` = Hafs, `projection_support`, `_meta.riwayah`.
- Specials strip uses `special_text("Basmala", riwayah)`; Warsh/Qalun Fatiha opener handled as `opening_basmala`.
- Guard: refuse a run whose riwayah is not in `SUPPORTED_RIWAYAT`.
- Depends on P4. Gate: qua batch suite + one real Warsh clip.

### Q3 — sidecar policy *(qua)*
- `qua_timing_batch` — skip `low_confidence_v2` + `ts_validation` for non-Hafs (D12); keep `auto_split_v1` (D11), `hidden_pause_v1`, `false_split_v1`; `unmarked_wasl_v1` verse-join uses target ayah numbering.
- `pipeline_meta.json` gains `riwayah`.

### Q4 — word-profile shard producer *(qua)*
- `qua_timing_engine.timestamps` — accept `riwayah` on `/internal/v1/timestamps`, word-only branch, emit the §4.1 document.
- `qua_sdk.integrations.shards` — `build_word_shards()` beside `build_native_shards()`.
- Depends on P3. Gate: qua timing suite + the 6-file corpus re-run; redeploy the batch timing Space.
- Unblocks P8.

### P5 — validation, edition-aware *(depends on P2)*
- `classifier.py` (4 sites, §5.1), `detail.py:535` (§5.2), `_structural.py:34`, `_missing.py:74,80`, `__init__.py:149,155`, `services/segments/{save,segments_query,stamping}.py`, `services/segments/qalqala.py`, `utils/repetitions.py`.
- Thread the riwayah from `cache.get_seg_meta(reciter)["riwayah"]` -> catalog delivery -> `to_sdk_slug`.
- `inspector/config.py` per-edition thresholds (§5.3).
- `routes/segments/data.py` `/api/seg/config?riwayah=`.
- `scripts/backfills/backfill_boundary_adj.py`, `scripts/backfills/purge_stale_wraps.py` — accept a riwayah.
- Gates: `test_classifier_hafs_output_unchanged`, `test_edition_tables_hafs_identity`, new `test_classifier_warsh.py`, `seg_validate.json` snapshot unchanged.

### P6 — Segments tab *(depends on P2, P5)*
- `qua_shared/schemas/wire/seg.py` — `SegReciter.riwayah`, `SegDataResponse.riwayah`. Regen.
- `styles/segments.css:241` — `font-family: var(--font-quran), ...`; `--font-quran` set per-edition on the tab root (token-driven, no raw values in components).
- **new** `lib/refs/edition-font.ts` — injects one `@font-face` per loaded non-Hafs edition pointing at `/api/static/edition/<riw>/font.ttf`. Never inlined base64 — that would add ~4 MB to every page; the Flask route has no LFS-smudge problem, which is the only reason DK is inlined.
- `tabs/segments/utils/data/references.ts:152` — U+06DD becomes `refs.verse_marker_prefix`.
- `tabs/segments/SegmentsTab.svelte:423` — `loadQuranRefs(riwayah)` on reciter switch.
- `tabs/segments/utils/data/config-loader.ts` — pass `?riwayah=`.
- Gates: FE vitest for ref-picker + cross-chapter traversal on a Warsh fixture (Warsh `72:16:8` editable although Hafs has 7 words there); RTL re-check.

### P7 — Timestamps tab, word profile *(depends on P2, P3)*
Gate on the **shard's own** `_meta.profile`, not the manifest riwayah.

| Surface | File | Action |
|---|---|---|
| Letters / Phonemes toggles | `TimestampsFooterAnalysis.svelte`, `stores/display.ts:30,33` | forced `false`, `disabled`, i18n'd title |
| Karaoke wipe | same, `stores/display.ts:50` | disabled — interpolates within a cell from sound timing that does not exist |
| Tajweed drop-up | `TajweedSettingsPanel.svelte`, `stores/tajweed-settings.ts` | disabled; the 45-rule catalogue is Hafs-phonemizer-generated |
| Analysis row | `TimedAnalysisRow.svelte` -> **new** `WordTimedRow.svelte` | word cells only; reuses the `--qc-*` tokens |
| Hover bus | `stores/display.ts:78` | `kind` restricted to `'word'` |
| Granularity | `stores/display.ts:19`, `recitation-animation/config.ts`, `NowReciting.svelte` | locked to `words`; char toggle hidden in **both** the teleprompter and the Dashboard now-reciting player |
| Shaped glyphs | `shaped-glyphs.ts` | **no change** — the existing fallback covers it (D10) |
| Ayah marker | `LineAnimation.svelte:43` `AYAH_END` | edition-derived prefix (D7) |
| Font | `styles/timestamps.css:30-31` `--qc-connected` | `var(--font-quran, 'DigitalKhatt', ...)` |
| ts-validation accordion | `TsValidationPanel.svelte` | verify it degrades on an absent sidecar |
| Proxy-timing badge | Timestamps header | one i18n'd badge: word timings from a Hafs proxy alignment |

Reports: restrict to `audio(verse,word)`, `timing(word,boundary)`, `silence(boundary)`, `other`;
`tajweed` and `phonemes` disabled.

Gates: FE vitest on a word-profile shard fixture; `inspector-playwright` screenshot in light + dark.

### P8 — timestamps run request *(depends on Q4 deployed)*
- `ts_space_client.py:118-128` — `body["riwayah"] = sdk_slug` **only when non-Hafs**. WAS: because the JCS canonicaliser sorts keys and the field is omitted for Hafs, every existing Hafs preimage stays byte-identical, so the QUA and Space changes only have to ship together for non-Hafs.
- `services/admin/timestamps_jobs.py::launch` — thread the delivery riwayah.
- Gates: signer parity test extended with a non-Hafs body; a live dev-Space run.

### P9 — requests copy *(depends on P0 only)*
- `RequestForm.svelte:207-208,316,474` and `submit/StepDetails.svelte:43-46,71,167` — `!isSupportedRiwayah(short)` from the generated constant.
- `tabs/dashboard/messages/{en,ar}.json` — retire `dashboard_request_non_hafs_callout`, add `dashboard_request_unsupported_riwayah_callout`, Arabic MSA per the `i18n` skill.
- Paraglide recompile; delete the generated `dashboard_request_non_hafs_callout.*`.
- No backend change — `intake_validation.py` has no Hafs gate today.

### P10 — releases + HF dataset *(off the critical path)*
- `qua_jobs/cut_release.py` — word-profile reciters emit **verse + word only**; redefine `content_hash` as the deepest emitted tier and record `tiers: ["verse","word"]` per recitation. Bump `RELEASE_FORMAT_MAJOR` to v4.0.0 per D20.
- `static_refs` in the dataset manifest carries the per-edition script + font.
- `_load_canonical_verses`/`select_complete_verses` — per-edition word counts.
- `qua_shared/timestamps_native.py` — word branch.
- `qua_shared/hf_dataset_catalog.py` — already config-per-riwayah; keep Inspector slugs as config names, record the SDK slug + `projection_sha256` in the config card. Mind the 30-splits-per-config cap.
- `release_changelog.py` + `docs/templates/release_body.md` — name the riwayah, state plainly that non-Hafs timings are Hafs-proxy word timings.
- `services/admin/automation/evaluators.py` — verify stale-TS-regen carries the job kind so a word-profile regen does not trip the native audit.

### P11 — reference docs *(land with the code that changes each surface)*
`shards.md`, `timestamps-job.md`, `validation.md`, `catalog.md`, `dataset-and-releases.md`,
`segments-editor.md`, `frontend.md`, `config-deploy.md`, `schemas.md`, plus a **new
`docs/reference/editions.md`** and its row in `docs/reference/README.md` + `CLAUDE.md`.

---

## 7. Cross-repo sequencing

Critical path:

```
Q1 (qua alias fix) ------\
                          >-- P1 (install plumbing) -- P2 (edition data) --+-- P5 (validation)
P0 (slug SSOT + docs) ---/                                                 +-- P6 (Segments)
                                                                           +-- P7 (Timestamps) -- VERIFY
P3 (v14 schema, readers) --------------------------------------------------/            ^
P4 (detailed.json) -- Q2 (extraction) -- Q4 (word-shard producer) -- P8 (ts_space_client) /
```

**Must land + be released in `qua` before the Inspector can run:**

| Inspector WP | Requires in `qua` |
|---|---|
| P1 | Q1 merged, and a commit SHA pinned in `qua_domain_pin.py` |
| P8 | Q4 merged **and the batch timing Space redeployed** on that pin |
| Real non-Hafs data | Q2 + Q3 merged and the Katana image rebuilt |

**Developable in parallel behind `INSPECTOR_MULTI_RIWAYAH`:** P0, P3, P4, P9 have no `qua`
dependency at all. P2, P5, P6, P7 need only the packaged `qua-domain` (P1), not any new upstream
code — the edition assets already exist at `qua@c334291`.

---

## 8. Files that change shape on the bucket

| Path | Change |
|---|---|
| `reciters/<slug>/detailed.json` | `_meta.riwayah`; segs gain `source_ref`, `projection_support` |
| `reciters/<slug>/timestamps/<ch>.json.br` | word-profile v14 for non-Hafs; **existing Hafs v13 untouched** |
| `reciters/<slug>/pipeline_meta.json` | `riwayah` |
| `reciters/<slug>/segments.json` | regenerated from `detailed.json` using the edition verse list |
| `reciters/<slug>/edit_history.jsonl` | no schema change; non-Hafs reciters are new, so no migration |
| `catalog/audio_manifest/<slug>.json` | already carries `_meta.riwayah` |
| WAS: `reference/editions/*` | **not created** — superseded by D6 |

---

## 9. Validation-category verdicts

| # | Category | Verdict | What changes |
|---|---|---|---|
| 1 | `failed` | keep | none |
| 2 | `missing_verses` | keep | per-edition verse list (Warsh/Qalun: 6,214 ayahs) |
| 3 | `missing_words` | keep | per-edition `verse_word_counts` |
| 4 | `structural_errors` | keep | `_structural.py:34` word counts |
| 5 | `low_confidence` | keep | per-edition threshold dict, all four equal at first release (D19) |
| 6 | `low_confidence_v2` | disabled for non-Hafs | sidecar not produced; registry row stays, count 0, accordion hides |
| 7 | `audio_bleeding` | keep | `seg_belongs_to_entry` containment in target coords |
| 8 | `boundary_adj` | keep, remap | word-granularity muqattaat + standalone tables + `single_word_verses` (28 -> 3 for Warsh) |
| 9 | `repetitions` | keep | pure `wrap_word_ranges` geometry |
| 10 | `cross_verse` | keep | target ayah numbering |
| 11 | `qalqala` | keep | `compute_qalqala_letter` -> `dk_text_for_ref(ref, riw)`; the letters are edition-invariant |
| 12 | `muqattaat` | keep, remap | word-granularity (`2:1:1` fires in Warsh, `2:1:2..8` do not) |
| 13 | `basmala_amin` | rework | §5.2 |
| 14 | `hidden_pause` | keep | none |
| 15 | `false_split` | keep | none |
| 16 | `unmarked_wasl` | keep | target ayah numbering |

No registry row is added or removed, so the `registry.py` <-> `registry.ts` parity test is untouched.

---

## 10. Test + gate plan

### Existing suites that break, and how

| Suite | Why | Fix |
|---|---|---|
| `snapshots/seg_config.json` | `/api/seg/config` gains `?riwayah` | Hafs response stays byte-identical; add `seg_config_warsh.json` |
| `snapshots/ts_manifest.json` | manifest gains `editions` | Emitted only when a non-Hafs reciter is advertised, so the Hafs-only fixture is unchanged |
| `snapshots/seg_reciters.json`, `seg_data.json` | `riwayah` field added | Re-capture; assert `"hafs_an_asim"` for every fixture |
| `test_capabilities.py`, `test_route_auth`, `test_dev_mode` | would break on a new capability | **No capability is added** (D16), so unchanged |
| registry parity tests | would break on a registry change | No registry change, so unchanged |
| `test_ts_shard_schema.py`, `test_ts_roundtrip.py` | `TsShardMeta` renamed | Back-compat alias keeps them green; extend with word-profile cases |
| `test_ts_target_snapshot.py` | version gate widened | Extend with a v14 word doc |
| `test_quran_refs.py` | payload becomes per-edition | Extend; Hafs payload byte-identical |
| `compact-shards.test.ts` | `!== 13` throw | Extend with a word-profile fixture |

### New tests

| Test | Asserts |
|---|---|
| `test_surah_info_matches_edition_index` | `data/surah_info.json` counts == `load_edition_index("hafs")` (6,236 verses / 77,433 words / 0 diffs) |
| `test_hafs_tables_are_projection_identity` | derived `muqattaat_words("hafs")`, `standalone_refs`, `standalone_words`, `single_word_verses` == `inspector/constants.py` exactly |
| `test_classifier_hafs_output_unchanged` | `classify_segment` over the committed fixtures is identical with and without `qua_domain` importable |
| `test_edition_tables_warsh` | the 4 shifted standalone refs, the dropped `(42,2)`, `single_word_verses == 3`, the changed skeleton |
| `test_basmala_amin_edition_rules` | Hafs emits the `1:1` sub-check; Warsh does not; both emit Amin + missed-Basmala |
| `test_ts_shard_word_profile` | round-trip byte-equality, forward-compat `_meta` extra, each closure failure |
| `test_word_shard_audit` | dense word ids, part closure, monotonic intervals, invalid target ref rejected, `words_sha256` mismatch rejected |
| `test_wbw_reverse_projection` | Warsh `1:1:1` gloss == Hafs `1:2:1` gloss; `40:26:13` joins two source glosses |
| `test_ts_space_client_riwayah_signature` | the Hafs preimage is unchanged; a Warsh body signs correctly |
| `test_editions_unavailable_fails_loudly` | with the accessor disabled, a non-Hafs delivery 503s and never renders Hafs |
| `test_qua_domain_pin_matches_dockerfile` | `qua_domain_pin.QUA_COMMIT` == the `ARG QUA_COMMIT` default |

`qua_domain`-requiring tests carry `@pytest.mark.skipif(not editions.available())` so a fork
without the PAT still gets a green suite.

### Replacing the drift gate

WAS: `docs/reference/validation.md` documents `bench/snapshot.py`, `bench/drift.py`,
`bench/measure.py` and `bench/ground_truth/<slug>.json` as the drift gate for any perf-sensitive
validation change. **Verified: no `bench/` tree exists in this repo or on disk** — `git ls-files`
matches only `scripts/diagnostics/bench_storage.py`, and the doc's own closing note admits the tree
"lives outside the inspector working tree". It therefore cannot gate this work. The equivalent
guarantee comes from three cheaper, real gates:

1. **Identity proof** — `test_hafs_tables_are_projection_identity` + `test_surah_info_matches_edition_index`. A coordinate-table edit that changes any Hafs value fails here. Strictly stronger than a snapshot: it proves the derivation, not one sample.
2. **Fixture-level classifier parity** — `test_classifier_hafs_output_unchanged` over the committed segment fixtures with the accessor forced unavailable, proving the persisted-classifier-field writers still agree.
3. **Nightly bucket validation** — `bucket-validate.yml` + `/healthz?deep=1` already round-trips a bucket sample through `qua_shared/schemas`. Extend the sample to include one non-Hafs slug once one exists.

**Non-Hafs ground truth** does not exist until Q2 runs. Order: land P5 behind the identity gates
(which fully protect the 37 Hafs reciters), produce the first real Warsh delivery, then commit its
`detailed.json` + `seg_validate` output as a per-edition fixture pair under
`inspector/tests/fixtures/` — not a resurrection of `bench/`.

---

## 11. Verification plan

Everything runs against the **dev** Space (`hetchyy/quranic-inspector-dev` / bucket
`hetchyy/quranic-inspector-bucket-dev`). Prod is untouched.

### 11.1 Deploy

```
python scripts/deploy/upload_inspector.py dev
```

Poll the Space runtime to `RUNNING`, then `GET /healthz` must report
`editions: ["hafs","warsh","qalun","shuba"]`, `status: ok`. If `editions: []`, the `QUA_SDK_PAT`
Space secret is missing or the build stage skipped — check the build log for `[qua-domain] no PAT`.

### 11.2 Synthetic delivery A — Segments tab, Warsh

WAS: the state is **`awaiting_review`** (claimable) then **`under_review`** (editable). There is no
`available_for_review` state — see `docs/reference/state-machine.md:25`.

```bash
S=.claude/skills/inspector-admin/scripts
python $S/admin_catalog.py reciter  add --id synthetic_warsh --name-en "Synthetic Warsh"
python $S/admin_catalog.py delivery add --slug synthetic_warsh_warsh_an_nafi_murattal \
       --reciter-id synthetic_warsh --riwayah warsh_an_nafi --style murattal \
       --source qul --audio-category by_surah --chapter-count 1
python $S/admin_state.py <slug> --event reciter.requested --reason "multi-riwayah verification"
python scripts/devenv/make_synthetic_delivery.py <slug> --riwayah warsh --chapter 112
python $S/admin_state.py <slug> --event reciter.alignment_completed
python $S/admin_state.py <slug> --event reciter.claimed
```

`scripts/devenv/make_synthetic_delivery.py` (new, P1) writes, using `qua_domain` directly:

| Object | Content |
|---|---|
| `reciters/<slug>/detailed.json` | `_meta.riwayah = "warsh_an_nafi"`; segs with **Warsh** `matched_ref`s from `load_edition_index("warsh")`, `source_ref` from the reverse projection, plausible times over a 30 s silent MP3 |
| `reciters/<slug>/segments.json` | verse-aggregated view derived from the above |
| `reciters/<slug>/pipeline_meta.json` | `riwayah`, `deleted_basmala_chapters: []` |
| `catalog/audio_manifest/<slug>.json` | `_meta.riwayah`, one chapter, a public CDN URL |
| sidecars | none — proves the absent-sidecar degradation |

Expect: Segments renders Warsh text in the Warsh font; ayah markers are bare Arabic-Indic digits
(no U+06DD); the ref picker offers `112:1:1..4`; `low_confidence_v2` and `ts_validation` accordions
are hidden; `muqattaat` fires only on `2:1:1` for a surah-2 fixture.

### 11.3 Synthetic delivery B — Timestamps tab, Qalun, published

Steps as above with `--riwayah qalun`, plus:

```bash
python scripts/devenv/make_synthetic_delivery.py <slug> --riwayah qalun --chapter 112 --with-word-shards
python $S/admin_state.py <slug> --event reciter.marked_ready
python $S/admin_state.py <slug> --event reciter.published
```

`--with-word-shards` writes `reciters/<slug>/timestamps/112.json.br` through
`write_validated_shard`, so the new word audit gates the fixture itself: one reading,
`parts=[["112:1",0,3000,0,4]]`, four `words` rows in Qalun text and coordinates, four `boundaries`
with the last carrying `verse_end: 1`.

Expect: the reciter appears in the Timestamps dropdown; the analysis view renders word cells in the
Qalun font; Letters/Phonemes/Wipe/Tajweed are disabled with explanatory titles; the proxy-timing
badge shows; the teleprompter animates word-by-word with no shaped SVG; the report drop-up offers
only `audio` / `timing(word,boundary)` /
`silence` / `other`.

### 11.4 Screenshots + regression

- `inspector-playwright` skill: the Qalun verse in light and dark.
- Re-check one existing Hafs reciter end-to-end (Segments + Timestamps + a report) and diff `/api/static/quran-refs.json` bytes against the pre-deploy Space — must be identical.
- Tear down both fixtures with `reciter.discarded`.

---

## 12. `@quranic-phonemizer/cells` — located, unchanged

| Fact | Value |
|---|---|
| Declared | `inspector/frontend/package.json` -> `"@quranic-phonemizer/cells": "github:Hetchy/quranic-phonemizer-web#cells-v2.2.5"` |
| Resolved | `package-lock.json:1052` -> `git+ssh://git@github.com/Hetchy/quranic-phonemizer-web.git#ff1fb6a8...` |
| Install auth | `CELLS_DEPLOY_KEY` SSH deploy key — Dockerfile stage 1 (`inspector/Dockerfile:31-40`), `upload_inspector.py:293-298`, `inspector-checks.yml:32-41`, `docker-publish.yml:95` |
| Release model | subtree-split tag `cells-vX.Y.Z` from `Hetchy/quranic-phonemizer-web`; package major == the phonemizer wire `schema_version` |
| Re-pin procedure | bump the `#cells-vX.Y.Z` fragment in `package.json`, `npm install` to refresh the resolved commit in `package-lock.json`, commit both |

**Change required for multi-riwayah: none, and no version bump.** Reasons:

- The package's contract is "render the versioned native cell wire". A word-profile row has no columns, sounds, groups, rule occurrences, or bridges; `parse()`/`parseCompact()` validate both `analysis.schema_version` and `cells.schema_version` and throw on anything else. Synthesising a degenerate wire would be a lie the audit could not catch.
- **Per-edition font is already a host concern**: the package exposes `--qc-*` custom properties and does not inspect a theme. The Inspector sets `--qc-arabic` / `--qc-connected` at `styles/timestamps.css:30-31`; P7 points those at `--font-quran`.
- **The ayah marker is drawn by the Inspector** in the surfaces that survive for word profile — `LineAnimation.svelte:43` and `tabs/segments/utils/data/references.ts:152`. The package's own verse marker lives inside `AnalysisRow`, which word profile does not use.

**When the package *would* change:** if a **native** non-Hafs phonemizer ever ships. That is a
producer schema bump, hence a `cells` major (`3.x`), a new tag, and the re-pin procedure above.
Out of scope; the timing contract is Hafs-proxy word-only.

---

## 13. Risk register

| # | Risk | Guard |
|---|---|---|
| R1 | Swapping Hafs display text onto `qua_domain`'s `hafs.words` changes 44,481 word glyphs, silently shifting every `dk_text_for_ref` derivation, every `qalqala_letter`, every `STANDALONE_WORDS` skeleton match | D5: Hafs never reads `qua_domain` text. Enforced by a test that `editions.word_map("hafs")` raises — the Hafs branch must go through `get_dk_words_flat()` |
| R2 | Edition word counts diverge from `surah_info.json` and re-stamp `is_boundary_adj` / `missing_words` on 37 reciters | Verified equal (0 diffs) + `test_surah_info_matches_edition_index` in CI; Hafs runtime path unchanged (D4) |
| R3 | A verse-granularity projection of `MUQATTAAT_VERSES` suppresses `boundary_adj` across a whole Warsh merged verse | D1 word-granularity + `test_hafs_tables_are_projection_identity` |
| R4 | A per-edition cache key is forgotten and Warsh word counts serve a Hafs reciter | Every new cache keyed on SDK slug; the `quran-refs` sessionStorage key includes the riwayah; a test asserts two editions produce different `payload_hash()` |
| R5 | `qua_domain` absent in prod (PAT rotated) so a Warsh delivery renders Hafs coordinates and a reviewer saves wrong refs | D17: fail loudly (503), never fall back. `test_editions_unavailable_fails_loudly` |
| R6 | Restamping the 37 reciters' v13 shards | D2: never restamped. Native meta accepts `Literal[13,14]`; `shard_profile` defaults to `"native"` |
| R7 | Adding `riwayah` to the `ts_space_client` body changes the HMAC preimage for existing Hafs runs | Field omitted for Hafs; JCS sorts keys, so the Hafs preimage is byte-identical. Pinned by the signer parity test |
| R8 | `qua_domain` pin drifts from the Dockerfile `ARG` default | `test_qua_domain_pin_matches_dockerfile` + a boot assertion on `projection_asset_info().sha256` |
| R9 | A new anon-eligible capability breaks three hardcoded test lists | D16: no new capability |
| R10 | `validation.md` keeps promising a drift harness nobody can run, so the next agent skips a real gate | D13 / P0: replace with the three real gates |
| R11 | The sparse clone pulls the whole pack on a slow builder and times out the Space build | `--filter=blob:none --depth 1 --no-checkout` + `sparse-checkout`; the stage is cached and independent of the app layers |
| R12 | A Warsh delivery is admitted by `auto_detect` before P5 lands, stamping Hafs-derived `is_boundary_adj` into `detailed.json` | The admission guard refuses non-Hafs while `INSPECTOR_MULTI_RIWAYAH=0`; keep it `0` on prod until P5+P6 are verified on dev |
