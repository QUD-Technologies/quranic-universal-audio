# Vendored packages

Private packages the frontend installs from tarballs (`package.json` `file:vendor/...`), so
`npm ci` needs no registry credentials. `package-lock.json` pins each tarball's integrity.

## Mushaf (`@qud-technologies/mushaf-*` 0.2.22)

The Timestamps Mushaf view (`src/tabs/timestamps/mushaf/`) draws with these: the release
tarballs of [`QUD-Technologies/qud-mushaf`](https://github.com/QUD-Technologies/qud-mushaf)
`pkg-v0.2.22` (sha256 as in the release's `SHA256SUMS`). `package.json` `overrides` keep web's and
svelte's dependency on core (and svelte's on web) on these files, not the registry.

| Tarball | sha256 |
|---|---|
| `qud-technologies-mushaf-core-0.2.22.tgz` | `4c4e9740f4e40de6af4c62681897db00b652a2b997c0ab22d5e75627d83a4659` |
| `qud-technologies-mushaf-web-0.2.22.tgz` | `25d17fc84bfab092f7665f5f9271a0059c020614e064cce24541a54f78227b21` |
| `qud-technologies-mushaf-svelte-0.2.22.tgz` | `0185af055e9518d2cb8446a8dbfc98ed244517bea2c56970045a28c9ef958e0f` |

Repin: download the new release's tarballs (`gh release download pkg-v<version> -R
QUD-Technologies/qud-mushaf`), replace the files here, update this table and `package.json`, then
`npm install` to refresh the lock.

Their peer `@qud-technologies/quran-data` (the Talqeen TS SDK) is **not** vendored: it is a
release asset of `talqeen-ai-data`, fetched into `../.quran-data/` by
`scripts/devenv/install_quran_data.py` (pin and sha256 in `qua_shared/quran_data_pin.py`).
Run that before `npm ci`.

## `quranic-phonemizer-cells-4.1.0.tgz`

`@quranic-phonemizer/cells`, the analysis grid's cell renderer.
