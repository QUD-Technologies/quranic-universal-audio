"""The pinned ``QUD-Technologies/talqeen-ai-data`` revision the Hafs reference data comes from.

The Hafs word text (Digital Khatt v2), ayah-end markers and surah metadata are read from
the Talqeen Quran data pack through its Python SDK, ``talqeen-quran-data``. Neither the SDK
nor the pack is on PyPI or in this repository: both are fetched from the private data repo
at this exact revision by ``scripts/devenv/install_quran_data.py`` (local setup, CI and the
image build alike). The frontend's TypeScript SDK and the fonts are release assets of the same
repository, fetched by the same installer. This module is the single place all of it is pinned.

Bump it like a lockfile entry: change ``TALQEEN_DATA_REF`` to the new release tag (and
``PACK_SHA256`` when the pack changed), run ``python scripts/devenv/install_quran_data.py``,
then the ``scripts/diagnostics/quran_data_integrity.py`` capture/compare gate, and commit.

Credentials never live here. CI and the image build use ``TALQEEN_DATA_READ_TOKEN`` (the
same secret ``talqeen-ai-mizan`` uses); locally the installer falls back to ``GH_TOKEN`` or
the existing ``gh`` login.
"""

from __future__ import annotations

TALQEEN_REPO = "QUD-Technologies/talqeen-ai-data"
TALQEEN_HTTPS_URL = f"https://github.com/{TALQEEN_REPO}.git"

#: The release tag the SDK and pack come from (``sdk-v1.20.0``; its pack is ``data-v1.12.3``'s).
TALQEEN_DATA_REF = "sdk-v1.20.0"

#: SDK distribution and the version the pinned revision builds.
SDK_DIST_NAME = "talqeen-quran-data"
SDK_VERSION = "1.20.0"
#: Path of the Python SDK inside the data repo (a sparse checkout target).
SDK_PATH = "sdk/python"

#: The core pack: words, surahs, divisions and layouts, no recitation sounds.
PACK_NAME = "quran-core.qref"
PACK_PATH = f"data/{PACK_NAME}"
PACK_SHA256 = "75dcd7e8a46e118b7e642adae8bc6fd6b4e6afeae088d95ceb39bbf6db5d0e86"

#: The TypeScript SDK the frontend builds against: a release asset of ``TS_SDK_RELEASE``,
#: fetched into ``inspector/frontend/.quran-data/`` (``package.json`` installs it from there).
TS_SDK_RELEASE = "sdk-v1.20.0"
TS_SDK_FILE = "qud-technologies-quran-data-1.20.0.tgz"
TS_SDK_SHA256 = "d6a856ff287f124ffc56a60a50ef3d01bd6e6c8984dc604ae863619cbc653ee6"

#: The data release whose ``manifest.json`` says where each font asset lives.
DATA_RELEASE = "data-v1.12.3"
#: The fonts the pack's ``fonts()`` names (text, surah name, surah frame), fetched next to the
#: pack under ``fonts/`` and served to the browser by ``/api/static/quran-data/<file>``.
FONTS_DIR_NAME = "fonts"
FONT_SHA256 = {
    "DigitalKhattV2.otf": "b739d35562ea8bd28367a0cb069f993f025e6d7c8b6882eae01456fd82c0e9ef",
    "DigitalKhattV2.woff2": "8b162f68e7e000e333fb235a37a5954bdc09dac018ae7aaf7b8b5b30a5fad67a",
    "DigitalKhattV1.woff2": "e0a7ab558f4948fc70bb542e0f74628abc3feb47e0654dfcaed3dba1a6843c51",
    "SurahNameV2.woff2": "7d43fb3adddd53f754ae4d252db54650b14466de3f7692043631e3460a628857",
    "QuranCommon.woff2": "d5ce57bdf26de535bed7fed159764c10316d8d16404c20ceed52d83daa33211e",
    "ornaments.json": "a0f2d9f1985e707de10315458daa107720d9467e41ab8815653207388e8efcbb",
}

#: Justified-line drawings per layout and the glyph outline tables of their fonts (brotli), read
#: by the Mushaf view's justify (``justifyBase``: ``/api/static/quran-data/justify/<name>.bin``,
#: served from ``<name>.bin.br`` with ``Content-Encoding: br``). Fetched through ``DATA_RELEASE``'s
#: ``manifest.json`` into ``<pack dir>/justify/``.
JUSTIFY_DIR_NAME = "justify"
JUSTIFY_SHA256 = {
    "madani-1405.bin.br": "3c9ea9cefe62119620e0d6a46e58968fd4c60e6869e209143f6540204a276589",
    "madani-1421.bin.br": "205590e46ef617875de84ff1f50b10d28e6886ec9d72664a12e942d45c8f9e8d",
    "madani-1441.bin.br": "f3dc1de88f34c7a1da64cce9e0c1a7b5f3a105e7cf51abcc423a5ab08ed47d07",
    "outlines-86fc18f6.bin.br": "6cea6738a7dfda7610651cf2604dcd3763ff1c8d7f25bfb1e334ce9f049ba068",
    "outlines-b739d355.bin.br": "83a5ee513954b128e00a92893ae388b8b5b022107a2db641a7601fbde3380e4a",
}

__all__ = [
    "JUSTIFY_DIR_NAME",
    "JUSTIFY_SHA256",
    "DATA_RELEASE",
    "FONTS_DIR_NAME",
    "FONT_SHA256",
    "TS_SDK_FILE",
    "TS_SDK_RELEASE",
    "TS_SDK_SHA256",
    "PACK_NAME",
    "PACK_PATH",
    "PACK_SHA256",
    "SDK_DIST_NAME",
    "SDK_PATH",
    "SDK_VERSION",
    "TALQEEN_DATA_REF",
    "TALQEEN_HTTPS_URL",
    "TALQEEN_REPO",
]
