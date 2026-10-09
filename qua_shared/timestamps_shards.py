"""Timestamp shard builders and deterministic Brotli serialization."""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

MANIFEST_SCHEMA_VERSION = 1
#: Versions a reader accepts for a native document: 15, and 13 until every
#: reciter is re-timed.
#: There is no "version to write" constant here — the SDK builders stamp it, and
#: a second copy in this tree could only ever disagree with them.
NATIVE_SHARD_SCHEMA_VERSIONS = (13, 15)


def shard_profile(shard_doc: dict) -> str:
    """``"native"`` or ``"word"`` for a stored shard document.

    ``_meta.profile`` is absent on every v13 object, which predates the word
    profile — absent therefore means native. Every reader must go through here
    rather than branching on ``schema_version``, which does not distinguish
    the two.
    """
    return (shard_doc.get("_meta") or {}).get("profile") or "native"


def parse_shard(shard_doc: dict):
    """Validate a stored shard into its profile's model."""
    from qua_shared.schemas.bucket.ts_shard import TsShardDoc, TsWordShardDoc

    if shard_profile(shard_doc) == "word":
        return TsWordShardDoc.model_validate(shard_doc)
    return TsShardDoc.model_validate(shard_doc)


def build_timestamp_shards(
    v2_doc: dict,
    *,
    audio_category: str,
    src_meta: dict | None = None,
) -> dict[int, dict]:
    """Build strict native v13 shards through the staged SDK."""
    from qua_sdk.integrations.shards import build_native_shards

    return build_native_shards(
        v2_doc,
        audio_category=audio_category,
        src_meta=src_meta,
    )


def brotli_shard(shard_doc: dict) -> bytes:
    """Serialize and Brotli-compress a shard deterministically at quality 6."""
    import brotli
    import orjson

    payload = orjson.dumps(shard_doc)
    return brotli.compress(payload, quality=6, mode=brotli.MODE_TEXT)


def validated_brotli_shard(shard_doc: dict) -> bytes:
    """Audit a shard under its own profile and prove deterministic serialization."""
    if shard_profile(shard_doc) == "word":
        from qua_shared.timestamps_word_audit import audit_word_document

        audit_word_document(shard_doc)
    else:
        from qua_shared.timestamps_v13_audit import audit_v13_document

        audit_v13_document(shard_doc)
    payload = brotli_shard(shard_doc)
    if brotli_shard(shard_doc) != payload:
        raise RuntimeError("non-deterministic timestamp shard serialization")
    return payload


def write_validated_shard(path: Path, shard_doc: dict) -> bytes:
    """Atomically replace ``path`` with an audited deterministic shard."""
    payload = validated_brotli_shard(shard_doc)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        Path(temporary).replace(path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return payload


def sha256_hex(b: bytes) -> str:
    """SHA-256 hex digest of `b`. Used by the manifest's shard_hashes index."""
    return hashlib.sha256(b).hexdigest()
