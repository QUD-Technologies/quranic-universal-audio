# Vendored packages

Private packages the frontend installs from tarballs (`package.json` `file:vendor/...`), so
`npm ci` needs no registry credentials. `package-lock.json` pins each tarball's integrity.

## Mushaf (`@qud-technologies/mushaf-*` 0.1.0)

The Timestamps Mushaf view (`src/tabs/timestamps/mushaf/`) draws with these. Built with
`npm run build` then `npm pack` per package from
[`QUD-Technologies/qud-mushaf`](https://github.com/QUD-Technologies/qud-mushaf) branch
`feat/phase-1` at commit `6a498a85ab99555360aadcecb700149a03358bb3` (not yet released).

| Tarball | sha256 |
|---|---|
| `qud-technologies-mushaf-core-0.1.0.tgz` | `f270c41c7a54cbec51c086f906765473129013a8e792d19f6fd3ca6524e02202` |
| `qud-technologies-mushaf-web-0.1.0.tgz` | `4fcb1bf46b3b953d2971b75e349fdd89f4c4875af4065c8cb0acf16bf7180ac8` |
| `qud-technologies-mushaf-svelte-0.1.0.tgz` | `ea0deb2932cfef8fb3d7ba6c53166b9c1622d6f812a8a7e6859cc6f7e814447b` |

Repin: rebuild and `npm pack` the three packages at the new commit (or download a release's
tarballs), replace the files here, update this table, then `npm install` to refresh the lock.

Their peer `@qud-technologies/quran-data` (the Talqeen TS SDK) is **not** vendored: it is a
release asset of `talqeen-ai-data`, fetched into `../.quran-data/` by
`scripts/devenv/install_quran_data.py` (pin and sha256 in `qua_shared/quran_data_pin.py`).
Run that before `npm ci`.

## `quranic-phonemizer-cells-4.1.0.tgz`

`@quranic-phonemizer/cells`, the analysis grid's cell renderer.
