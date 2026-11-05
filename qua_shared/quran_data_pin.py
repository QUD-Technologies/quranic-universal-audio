"""The pinned ``QUD-Technologies/talqeen-ai-data`` revision the Hafs reference data comes from.

The Hafs word text (Digital Khatt v2), ayah-end markers and surah metadata are read from
the Talqeen Quran data pack through its Python SDK, ``talqeen-quran-data``. Neither the SDK
nor the pack is on PyPI or in this repository: both are fetched from the private data repo
at this exact revision by ``scripts/devenv/install_quran_data.py`` (local setup, CI and the
image build alike). This module is the single place the revision is written down.

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

__all__ = [
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
