"""The ``qua-domain`` pin is written in three places; they must not drift.

``qua_shared/qua_domain_pin.py`` is the source. The installer reads it. The
Dockerfile CANNOT read it — HF Spaces build with no build args, so its
``ARG QUA_COMMIT`` default is the pin as far as a deployed Space is concerned.
That duplication is unavoidable, so it is pinned here instead.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from qua_shared.qua_domain_pin import QUA_COMMIT, QUA_PACKAGE_PATH, QUA_SSH_URL

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = ROOT / "inspector" / "Dockerfile"
INSTALLER = ROOT / "scripts" / "devenv" / "install_qua_domain.py"


def test_commit_is_a_full_sha():
    # A short sha or a branch name would make the "pinned" build reproducible
    # only by luck.
    assert re.fullmatch(r"[0-9a-f]{40}", QUA_COMMIT)


def test_dockerfile_arg_default_matches_the_pin():
    text = DOCKERFILE.read_text(encoding="utf-8")
    match = re.search(r"^ARG QUA_COMMIT=([0-9a-f]{40})$", text, re.MULTILINE)
    if match is None:
        pytest.fail("inspector/Dockerfile has no `ARG QUA_COMMIT=<sha>` line")
    assert match.group(1) == QUA_COMMIT


def test_dockerfile_fetches_the_pinned_package_from_the_pinned_remote():
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert QUA_SSH_URL in text
    assert f"git sparse-checkout set --cone {QUA_PACKAGE_PATH}" in text


def test_the_optional_dependency_never_fails_a_build_that_lacks_the_key():
    # A fork, or any build without the secret, must still produce a working
    # Hafs-only image. Both halves of that are load-bearing:
    #   - the secret mount is `required=false`, so buildkit does not error;
    #   - the runtime install is guarded, so a missing wheel is not a failure.
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "--mount=type=secret,id=PRIVATE_REPO_TOKEN,required=false" in text
    assert "no qua-domain wheel — Hafs-only image" in text
    # buildx mounts a secret that resolved to "" as an empty file that EXISTS,
    # so the key test has to be -s. With -f a fork PR copies an empty key,
    # fails git auth and takes the whole image build down with it.
    assert "[ -s /run/secrets/PRIVATE_REPO_TOKEN ]" in text
    assert "[ -f /run/secrets/PRIVATE_REPO_TOKEN ]" not in text
    # And the installer exits 0 rather than raising when there is no credential.
    assert "return 0" in INSTALLER.read_text(encoding="utf-8").split("skipped —")[1]
