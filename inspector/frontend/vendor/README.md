# Vendored packages

Private packages the frontend installs from tarballs (`package.json` `file:vendor/...`), so
`npm ci` needs no registry credentials. `package-lock.json` pins each tarball's integrity.

## Mushaf (`@qud-technologies/mushaf-*` 0.1.0)

The Timestamps Mushaf view (`src/tabs/timestamps/mushaf/`) draws with these. Built with
`npm run build` then `npm pack` per package from
[`QUD-Technologies/qud-mushaf`](https://github.com/QUD-Technologies/qud-mushaf) `main` at commit `ae730fc` (not yet released).

| Tarball | sha256 |
|---|---|
| `qud-technologies-mushaf-core-0.1.0.tgz` | `33ac711aa8c918cba88ac5a854a501929cd89c5d5bd08acd39637157a6980162` |
| `qud-technologies-mushaf-web-0.1.0.tgz` | `38b57d6bbe57e629feeea9ca6b9b982ab0ed91f0f363c6841739685e04321161` |
| `qud-technologies-mushaf-svelte-0.1.0.tgz` | `806fb642d68e391a23fa284ed8e1b4cdb864cfb6b8cd2cc119518bca593c52f5` |

Repin: rebuild and `npm pack` the three packages at the new commit (or download a release's
tarballs), replace the files here, update this table, then `npm install` to refresh the lock.

Their peer `@qud-technologies/quran-data` (the Talqeen TS SDK) is **not** vendored: it is a
release asset of `talqeen-ai-data`, fetched into `../.quran-data/` by
`scripts/devenv/install_quran_data.py` (pin and sha256 in `qua_shared/quran_data_pin.py`).
Run that before `npm ci`.

## `quranic-phonemizer-cells-4.1.0.tgz`

`@quranic-phonemizer/cells`, the analysis grid's cell renderer.
