"""The pinned ``QUD-Technologies/qua`` revision the edition data is built from.

``qua-domain`` is not on PyPI — it is a package inside the private monorepo,
installed from an exact commit. This module is the single place that commit is
written down, so the local installer, the Docker build stage, and the boot-time
provenance assertion cannot disagree.

Bump it like a lockfile entry: change ``QUA_COMMIT``, run
``python scripts/devenv/install_qua_domain.py``, and commit. A test asserts the
Dockerfile's ``ARG QUA_COMMIT`` default matches this value, because HF Spaces
build without build args — the Dockerfile literal is the pin on the Space.

Credentials never live here. CI and the image build use ``PRIVATE_REPO_TOKEN``;
the installer also accepts the legacy ``QUA_DOMAIN_DEPLOY_KEY`` or an existing
developer git credential.
"""

from __future__ import annotations

QUA_REPO = "QUD-Technologies/qua"
QUA_SSH_URL = "git@github.com:QUD-Technologies/qua.git"

#: Exact commit on ``QUD-Technologies/qua@main`` the edition assets come from.
#: 29e0733 = "alias the Universal Audio vocabulary slugs" — the first revision
#: whose ``normalize_riwayah`` accepts all four Inspector vocabulary slugs.
QUA_COMMIT = "29e073322f8197f2af5ecdb95d09780d6655bec7"

#: Path of the package inside the monorepo (a sparse checkout target).
QUA_PACKAGE_PATH = "packages/quran-domain"

#: Installed distribution name.
QUA_DIST_NAME = "qua-domain"

__all__ = [
    "QUA_COMMIT",
    "QUA_DIST_NAME",
    "QUA_PACKAGE_PATH",
    "QUA_REPO",
    "QUA_SSH_URL",
]
