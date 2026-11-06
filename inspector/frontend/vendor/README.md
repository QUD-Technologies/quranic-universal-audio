# Vendored packages

Private packages the frontend installs from tarballs (`package.json` `file:vendor/...`), so
`npm ci` needs no registry credentials. `package-lock.json` pins each tarball's integrity.

## Mushaf (`@qud-technologies/mushaf-*` 0.1.0)

The Timestamps Mushaf view (`src/tabs/timestamps/mushaf/`) draws with these. Built with
`npm run build` then `npm pack` per package from
[`QUD-Technologies/qud-mushaf`](https://github.com/QUD-Technologies/qud-mushaf) branch
`feat/reading-highlight` (PR #8) at commit `0c2ddb80692b4e44d1523a84d1be9d5ae05615d2` (not yet released).

| Tarball | sha256 |
|---|---|
| `qud-technologies-mushaf-core-0.1.0.tgz` | `d64381bd4ed00b20d05887517caef3ac7fa162eefc73791472ead1e281333fb2` |
| `qud-technologies-mushaf-web-0.1.0.tgz` | `29d738a1e1e9175318924b12d13b0cc2566e7149fa5881e72480e9da5feb56d4` |
| `qud-technologies-mushaf-svelte-0.1.0.tgz` | `9cbca28f6ee1d2f623b00e043ab4ed4a3feef7a5725efdb64e7ac528bc490ebe` |

Repin: rebuild and `npm pack` the three packages at the new commit (or download a release's
tarballs), replace the files here, update this table, then `npm install` to refresh the lock.

Their peer `@qud-technologies/quran-data` (the Talqeen TS SDK) is **not** vendored: it is a
release asset of `talqeen-ai-data`, fetched into `../.quran-data/` by
`scripts/devenv/install_quran_data.py` (pin and sha256 in `qua_shared/quran_data_pin.py`).
Run that before `npm ci`.

## `quranic-phonemizer-cells-4.1.0.tgz`

`@quranic-phonemizer/cells`, the analysis grid's cell renderer.
