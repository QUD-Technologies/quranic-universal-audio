# Vendored packages

Private packages the frontend installs from tarballs (`package.json` `file:vendor/...`), so
`npm ci` needs no registry credentials. `package-lock.json` pins each tarball's integrity.

## Mushaf (`@qud-technologies/mushaf-*` 0.1.0)

The Timestamps Mushaf view (`src/tabs/timestamps/mushaf/`) draws with these. Built with
`npm run build` then `npm pack` per package from
[`QUD-Technologies/qud-mushaf`](https://github.com/QUD-Technologies/qud-mushaf) branch
`feat/phase-1` at commit `6bf3c9c54659bc88d3572454a3519a0a697a9bfa` (not yet released).

| Tarball | sha256 |
|---|---|
| `qud-technologies-mushaf-core-0.1.0.tgz` | `90e7ca3a8b291f4739f4d02d026caf4ea8cf16f3228f996c864640a997a52b83` |
| `qud-technologies-mushaf-web-0.1.0.tgz` | `336296942f3a162cef5fb78463e6e59264489a1d0bab26daf275c51ec0503b2e` |
| `qud-technologies-mushaf-svelte-0.1.0.tgz` | `2b2edfdbe9382d1e23ff6f9ffabc969b53f022284805ef2359b2dda6038c89db` |

Repin: rebuild and `npm pack` the three packages at the new commit (or download a release's
tarballs), replace the files here, update this table, then `npm install` to refresh the lock.

Their peer `@qud-technologies/quran-data` (the Talqeen TS SDK) is **not** vendored: it is a
release asset of `talqeen-ai-data`, fetched into `../.quran-data/` by
`scripts/devenv/install_quran_data.py` (pin and sha256 in `qua_shared/quran_data_pin.py`).
Run that before `npm ci`.

## `quranic-phonemizer-cells-4.1.0.tgz`

`@quranic-phonemizer/cells`, the analysis grid's cell renderer.
