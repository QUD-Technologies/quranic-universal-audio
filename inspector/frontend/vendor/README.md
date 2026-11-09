# Vendored packages

Private packages the frontend installs from tarballs (`package.json` `file:vendor/...`), so
`npm ci` needs no registry credentials. `package-lock.json` pins each tarball's integrity.

## Mushaf (`@qud-technologies/mushaf-*` 0.2.21)

The Timestamps Mushaf view (`src/tabs/timestamps/mushaf/`) draws with these: the release
tarballs of [`QUD-Technologies/qud-mushaf`](https://github.com/QUD-Technologies/qud-mushaf)
`pkg-v0.2.21` (sha256 as in the release's `SHA256SUMS`). `package.json` `overrides` keep web's and
svelte's dependency on core (and svelte's on web) on these files, not the registry.

| Tarball | sha256 |
|---|---|
| `qud-technologies-mushaf-core-0.2.21.tgz` | `fb2a1d9674a01e6afae672a6bfa8af2a474fd4a94641ccfe0620c372e19e9119` |
| `qud-technologies-mushaf-web-0.2.21.tgz` | `a0aee1d359581f05fc6ac0785b2345b13a8e50656ef1224dedffaaf116df1ed2` |
| `qud-technologies-mushaf-svelte-0.2.21.tgz` | `5d8d84133023e55dc1e86ffbe63fb2a453047baf77ede9f3bb86c1500c621779` |

Repin: download the new release's tarballs (`gh release download pkg-v<version> -R
QUD-Technologies/qud-mushaf`), replace the files here, update this table and `package.json`, then
`npm install` to refresh the lock.

Their peer `@qud-technologies/quran-data` (the Talqeen TS SDK) is **not** vendored: it is a
release asset of `talqeen-ai-data`, fetched into `../.quran-data/` by
`scripts/devenv/install_quran_data.py` (pin and sha256 in `qua_shared/quran_data_pin.py`).
Run that before `npm ci`.

## `quranic-phonemizer-cells-4.1.0.tgz`

`@quranic-phonemizer/cells`, the analysis grid's cell renderer.
