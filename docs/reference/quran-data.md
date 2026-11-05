# Hafs reference data (`talqeen-quran-data`)

The Hafs words, ayah-end markers and surah metadata come from the Talqeen Quran data pack
(`quran-core.qref`) through its Python SDK, `talqeen-quran-data`, both from the private
`QUD-Technologies/talqeen-ai-data` repo. Nothing of it is committed here. Non-Hafs editions
are separate: `qua-domain` via `services/reference/editions.py` ([editions.md](editions.md)).

## Pin and install

| What | Where |
|---|---|
| Pin (data repo revision, SDK version, pack sha256) | `qua_shared/quran_data_pin.py`, the only copy |
| Installer (SDK + pack, or `--wheel-dir` for the image) | `scripts/devenv/install_quran_data.py` (run by `setup.sh backend`) |
| Pack location | `data/quran-core.qref` (gitignored), or `QUA_QURAN_PACK` |
| Credential | `TALQEEN_DATA_READ_TOKEN`, else `GH_TOKEN`, else the `gh` login |

The pin is a branch commit until the data release ships (`TODO(talqeen-ai-data#41)` in the
pin module): repin to the release tag, rerun the installer, rerun the integrity gate below.

Required, unlike `qua-domain`: without the pack the Inspector has no Hafs text. CI
(`inspector-checks.yml`) installs it in the backend and pyright jobs. The image builds it in
Dockerfile stage 1c (`quran-data-build`, build secret `TALQEEN_DATA_READ_TOKEN`), installs
the wheel and copies the pack to `/app/data`. `upload_inspector.py` sets the Space secret of
the same name; the build fails without it. No binary rides the staged Space tree.

## Readers — `qua_shared/quran_text.py`

Each rebuilds one retired bundled file from the pack, cached per process:

| Function | Replaces | Equality with the retired file |
|---|---|---|
| `surah_info()` / `surah_info_bytes()` | `data/surah_info.json` | byte-identical |
| `digital_khatt_script()` / `_bytes()` | `data/digital_khatt_v2_script.json` | deep-equal; bytes now in reading order |
| `hafs_word_script()` / `_bytes()` | `data/qpc_hafs.json(.gz)` | same keys, ids and fields; `text` is Digital Khatt |

Two legacy quirks are reproduced on purpose: QUL's DK ids give 37:130:3 and 37:130:4 one id
(QPC ids gave :4 the next one), and surah `name_ar` keeps the old shadda-first, decomposed
alif-madda spelling (the pack stores NFC).

Consumers: `services/storage/data_loader.py` (`load_dk`, `get_dk_words_flat`,
`get_word_counts`, `load_surah_info_lite`, `word_has_stop`), the Timestamps resources
(`/api/ts/resource/{digital_khatt,qpc_hafs}`, `services/reference/timestamps.py`), and HF-job
staging: `services/admin/jobs/base.py::staged_reference_files()` builds
`code/data/{surah_info,digital_khatt_v2_script}.json` from the pack, so `cut_release` and
`publish_hf` read the same documents and need neither SDK nor pack. Endpoints are unchanged.

## Text and stops

QPC Hafs text is retired: the `qpc_hafs` resource keeps its key and shape but carries the
Digital Khatt text (23,638 words differ from QPC, encoding conventions). `word_has_stop`
reads the Digital Khatt word (the pack's default, modern stops) against
`constants.STOP_SIGNS`. Versus QPC: 67 words lose a stop, 54 gain one, 160 change sign
(over every waqf mark U+06D6–U+06DB: 138 / 51 / 163). The full list is
[`data/hafs-stop-changes-qpc-to-dk.json`](data/hafs-stop-changes-qpc-to-dk.json). The
offline segment fixtures validate identically.

## Integrity gate

`scripts/diagnostics/quran_data_integrity.py capture DIR` on the old and the new code, then
`compare BEFORE AFTER`: `/api/surah-info`, word counts, `/api/static/quran-refs.json`, both
TS resources, cut_release's staged refs and their digests, stop changes, fixture validation.
Run it after any repin.
