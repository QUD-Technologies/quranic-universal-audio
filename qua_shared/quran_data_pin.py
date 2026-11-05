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

#: The release tag (``sdk-v1.9.0`` and ``data-v1.5.0`` name the same commit, cddf855).
TALQEEN_DATA_REF = "sdk-v1.9.0"

#: SDK distribution and the version the pinned revision builds.
SDK_DIST_NAME = "talqeen-quran-data"
SDK_VERSION = "1.9.0"
#: Path of the Python SDK inside the data repo (a sparse checkout target).
SDK_PATH = "sdk/python"

#: The core pack: words, surahs, divisions and layouts, no recitation sounds.
PACK_NAME = "quran-core.qref"
PACK_PATH = f"data/{PACK_NAME}"
PACK_SHA256 = "0a84227596677dbe4a262a4e656ec4b0d934f4a9150397241b45ef743c4474a6"

#: The TypeScript SDK the frontend builds against: a release asset of ``TS_SDK_RELEASE``,
#: fetched into ``inspector/frontend/.quran-data/`` (``package.json`` installs it from there).
TS_SDK_RELEASE = "sdk-v1.9.0"
TS_SDK_FILE = "qud-technologies-quran-data-1.9.0.tgz"
TS_SDK_SHA256 = "b034d8e44b159df53a4a11da8c17a3d00dc641aa3c9338e9cceb0dbc7086d216"

#: The data release whose ``manifest.json`` says where each font asset lives.
DATA_RELEASE = "data-v1.5.0"
#: The fonts the pack's ``fonts()`` names (text, surah name, surah frame), fetched next to the
#: pack under ``fonts/`` and served to the browser by ``/api/static/quran-data/<file>``.
FONTS_DIR_NAME = "fonts"
FONT_SHA256 = {
    "DigitalKhattV2.otf": "0935c48269a57c9808e52dfae47864c189396452901c689977156036a72dd217",
    "DigitalKhattV1.woff2": "ef97cc5e1a51b4f165b784c3614ca33190a3ab028e365453fb5825612f0b361c",
    "SurahNameV2.woff2": "7d43fb3adddd53f754ae4d252db54650b14466de3f7692043631e3460a628857",
    "QuranCommon.woff2": "d5ce57bdf26de535bed7fed159764c10316d8d16404c20ceed52d83daa33211e",
}

__all__ = [
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
