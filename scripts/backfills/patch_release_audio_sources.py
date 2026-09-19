#!/usr/bin/env python3
"""Patch a shipped GitHub release so playlist (YouTube/Drive) recitations carry
their NATIVE source URL + per-chapter offset in ``catalog.json`` instead of the
internal inspector bucket URL.

Background. Before the ``cut_release`` fix, the GH release ``catalog.json`` was
built from the audio manifest's ``chapters[*].url`` verbatim. For a combined
non-CDN source (one YouTube/Drive file serving several chapters) the manifest
stores a per-chapter *bucket* path in ``url`` and the real source in
``source_url`` (+ ``source_offset_ms``), so the release leaked the bucket URL
and dropped the offset. The HF dataset already resolved ``source_url`` per row;
this brings already-shipped GH releases in line and is a no-op once the cut fix
ships (re-running on a patched release finds nothing to change).

What it does, per affected recitation (one whose bucket manifest carries a
``source_url`` or a non-zero ``source_offset_ms`` on any chapter):

1. rebuilds the in-zip ``catalog.json`` ``audio.chapter_urls`` from the bucket
   manifest's native ``source_url``/``url`` and adds ``audio.chapter_offsets_ms``
   (same resolution as ``cut_release._audio_sources_from_manifest``), keeping
   every other field byte-for-byte;
2. re-zips ``<slug>.zip`` (tier files untouched) and recomputes its sha256/bytes
   + the manifest ``content_hash`` (``sha256(letter_tier.gz || catalog.json)``);
3. rewrites the release-level ``catalog.json`` + ``manifest.json`` accordingly;
4. re-uploads only the changed assets to the SAME release tag (``--apply``).

Safety gates abort before any upload if the deterministic zip re-pack doesn't
reproduce the shipped zip byte-for-byte, if a recomputed ``content_hash``
doesn't match the shipped manifest, or if a no-change round-trip of
``catalog.json`` / ``manifest.json`` isn't byte-identical — i.e. it only ever
ships bytes it can prove it reproduced.

The bucket manifests are read from the mount (``INSPECTOR_BUCKET_MOUNT`` or
``--bucket-mount``); pass the PROD bucket mount since releases are cut from prod.

Usage (dry-run prints the plan; ``--apply`` performs the upload)::

    python scripts/backfills/patch_release_audio_sources.py --tag v2.0.0 \
        --bucket-mount ~/qua-prod-bucket
    python scripts/backfills/patch_release_audio_sources.py --tag v2.0.0 \
        --bucket-mount ~/qua-prod-bucket --apply
"""

from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from qua_jobs.cut_release import (  # noqa: E402
    _audio_sources_from_manifest,
    _json_model_bytes,
    _pack_recitation_zip,
    _sha256_hex,
)
from qua_shared.schemas import (  # noqa: E402
    ReleaseCatalog,
    ReleaseManifest,
    ReleaseRecitationCatalog,
)

_TIER_FILES = (
    "verse_timestamps.json.gz",
    "word_timestamps.json.gz",
    "letter_timestamps.json.gz",
)


def _gh(*args: str, repo: str, capture: bool = True) -> str:
    """Run a ``gh`` subcommand, raising on failure."""
    cmd = ["gh", *args, "--repo", repo]
    proc = subprocess.run(cmd, capture_output=capture, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed: {proc.stderr.strip()[:400]}")
    return proc.stdout


def _latest_tag(repo: str) -> str:
    out = _gh("release", "view", "--json", "tagName", repo=repo)
    return json.loads(out)["tagName"]


def _download_asset(repo: str, tag: str, name: str, dest_dir: Path) -> Path:
    dest = dest_dir / name
    if dest.exists():
        dest.unlink()
    _gh("release", "download", tag, "--pattern", name, "--dir", str(dest_dir), repo=repo)
    if not dest.exists():
        raise RuntimeError(f"{name} not found in release {tag}")
    return dest


def _load_bucket_manifest(bucket_mount: Path, slug: str) -> dict | None:
    path = bucket_mount / "catalog" / "audio_manifest" / f"{slug}.json"
    if not path.exists():
        return None
    return json.loads(path.read_bytes())


def _zip_members(zip_bytes: bytes) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for name in zf.namelist():
            out[name] = zf.read(name)
    return out


def _fixed_audio_block(
    existing_audio: dict, bucket_manifest: dict, slug: str
) -> tuple[dict, int, int]:
    """Return ``(new_audio, n_urls_changed, n_offsets_added)``.

    Keys are constrained to the recitation's *shipped* ``chapter_urls`` so
    coverage can't drift if the bucket manifest changed since the cut; each key's
    URL/offset is re-resolved from the manifest (native source + offset), falling
    back to the shipped URL when the manifest lacks the chapter.
    """
    native_urls, native_offsets = _audio_sources_from_manifest(slug, bucket_manifest)
    old_urls: dict[str, str] = existing_audio.get("chapter_urls", {})
    new_urls: dict[str, str] = {}
    new_offsets: dict[str, int] = {}
    urls_changed = 0
    for key, old_url in old_urls.items():
        new_url = native_urls.get(key, old_url)
        new_urls[key] = new_url
        if new_url != old_url:
            urls_changed += 1
        if key in native_offsets:
            new_offsets[key] = native_offsets[key]
    new_audio = dict(existing_audio)
    new_audio["chapter_urls"] = new_urls
    if new_offsets:
        new_audio["chapter_offsets_ms"] = new_offsets
    else:
        new_audio.pop("chapter_offsets_ms", None)
    return new_audio, urls_changed, len(new_offsets)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", help="Release tag (default: latest)")
    parser.add_argument("--repo", default="QUD-Technologies/quranic-universal-audio")
    parser.add_argument(
        "--bucket-mount",
        type=Path,
        help="Bucket mount root (default: $INSPECTOR_BUCKET_MOUNT)",
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=_REPO_ROOT / ".local" / "release-patch",
        help="Where release assets are downloaded + rebuilt",
    )
    parser.add_argument("--apply", action="store_true", help="Upload the patched assets")
    args = parser.parse_args(argv)

    import os

    bucket_mount = args.bucket_mount or (
        Path(os.environ["INSPECTOR_BUCKET_MOUNT"])
        if os.environ.get("INSPECTOR_BUCKET_MOUNT")
        else None
    )
    if not bucket_mount or not bucket_mount.exists():
        print(
            f"error: bucket mount not found ({bucket_mount}); pass --bucket-mount",
            file=sys.stderr,
        )
        return 2

    repo = args.repo
    tag = args.tag or _latest_tag(repo)
    work = args.work_dir / tag
    work.mkdir(parents=True, exist_ok=True)
    print(f"== patching {repo} release {tag} (bucket: {bucket_mount}) ==")

    catalog_path = _download_asset(repo, tag, "catalog.json", work)
    manifest_path = _download_asset(repo, tag, "manifest.json", work)
    root_catalog = json.loads(catalog_path.read_bytes())
    root_manifest = json.loads(manifest_path.read_bytes())

    # Gate 0: a no-change round-trip of both release-level files must be
    # byte-identical, else our serialization would mutate untouched bytes.
    cat_rt = _json_model_bytes(ReleaseCatalog.model_validate(root_catalog))
    if cat_rt != catalog_path.read_bytes():
        print("error: catalog.json no-op round-trip not byte-identical — aborting", file=sys.stderr)
        return 3
    man_rt = _json_model_bytes(ReleaseManifest.model_validate(root_manifest))
    if man_rt != manifest_path.read_bytes():
        print(
            "error: manifest.json no-op round-trip not byte-identical — aborting", file=sys.stderr
        )
        return 3

    recitations: list[dict] = root_catalog["recitations"]
    manifest_recs: dict = root_manifest["recitations"]

    patched_slugs: list[str] = []
    new_zip_bytes_by_slug: dict[str, bytes] = {}
    summary: list[dict] = []

    for rec in recitations:
        slug = rec["slug"]
        bm = _load_bucket_manifest(bucket_mount, slug)
        if not bm:
            continue
        native_urls, native_offsets = _audio_sources_from_manifest(slug, bm)
        # Affected iff the manifest expresses any provenance the shipped catalog
        # can't have: a swapped bucket URL (source_url) or a non-zero offset.
        chapters = bm.get("chapters") or {}
        has_provenance = any(
            (c.get("source_url") or (c.get("source_offset_ms") or 0) > 0)
            for c in chapters.values()
            if isinstance(c, dict)
        )
        if not has_provenance:
            continue

        zip_path = _download_asset(repo, tag, f"{slug}.zip", work)
        zbytes = zip_path.read_bytes()
        members = _zip_members(zbytes)

        # Gate 1: deterministic re-pack of the UNCHANGED members must reproduce
        # the shipped zip byte-for-byte (proves the pack path matches the cut).
        if _pack_recitation_zip(slug, members) != zbytes:
            print(f"error: {slug}: zip re-pack not byte-identical — aborting", file=sys.stderr)
            return 4

        letter_gz = members["letter_timestamps.json.gz"]
        old_catalog_bytes = members["catalog.json"]

        # Gate 2: our content_hash formula must reproduce the shipped manifest's.
        old_hash = _sha256_hex(letter_gz + old_catalog_bytes)
        shipped_hash = manifest_recs.get(slug, {}).get("content_hash")
        if shipped_hash and old_hash != shipped_hash:
            print(
                f"error: {slug}: content_hash formula mismatch "
                f"({old_hash[:12]} != {shipped_hash[:12]}) — aborting",
                file=sys.stderr,
            )
            return 5

        existing_catalog = json.loads(old_catalog_bytes)
        new_audio, n_url, n_off = _fixed_audio_block(existing_catalog.get("audio", {}), bm, slug)
        if n_url == 0 and "chapter_offsets_ms" not in new_audio:
            continue  # already native — nothing to do (idempotent re-run)

        new_catalog_dict = dict(existing_catalog)
        new_catalog_dict["audio"] = new_audio
        # Re-validate + canonical-serialize through the real model so the bytes
        # match what a future fixed cut would emit.
        new_catalog_bytes = _json_model_bytes(
            ReleaseRecitationCatalog.model_validate(new_catalog_dict)
        )

        new_members = dict(members)
        new_members["catalog.json"] = new_catalog_bytes
        new_zip = _pack_recitation_zip(slug, new_members)
        new_zip_bytes_by_slug[slug] = new_zip
        new_hash = _sha256_hex(letter_gz + new_catalog_bytes)

        # Update the release-level structures in place.
        rec["audio"] = json.loads(new_catalog_bytes)["audio"]
        mrec = manifest_recs[slug]
        mrec["sha256"] = _sha256_hex(new_zip)
        mrec["bytes"] = len(new_zip)
        mrec["content_hash"] = new_hash

        patched_slugs.append(slug)
        summary.append(
            {
                "slug": slug,
                "urls_fixed": n_url,
                "offsets_added": n_off,
                "content_hash": f"{old_hash[:10]} -> {new_hash[:10]}",
            }
        )

    if not patched_slugs:
        print("no affected recitations — release already carries native sources.")
        return 0

    print(f"\n{len(patched_slugs)} recitation(s) to patch:")
    for s in summary:
        print(
            f"  {s['slug']:42s} urls_fixed={s['urls_fixed']:<4d} "
            f"offsets_added={s['offsets_added']:<4d} content_hash {s['content_hash']}"
        )

    # Re-serialize the release-level files (only patched entries changed; the
    # rest are byte-identical, per Gate 0).
    new_catalog_bytes = _json_model_bytes(ReleaseCatalog.model_validate(root_catalog))
    new_manifest_bytes = _json_model_bytes(ReleaseManifest.model_validate(root_manifest))
    (work / "catalog.json").write_bytes(new_catalog_bytes)
    (work / "manifest.json").write_bytes(new_manifest_bytes)
    for slug, zb in new_zip_bytes_by_slug.items():
        (work / f"{slug}.zip").write_bytes(zb)

    if not args.apply:
        print(f"\n[dry-run] rebuilt assets written to {work}. Re-run with --apply to upload.")
        return 0

    print(f"\nuploading {len(patched_slugs) + 2} asset(s) to {tag} ...")
    upload = [work / "manifest.json", work / "catalog.json"]
    upload += [work / f"{slug}.zip" for slug in patched_slugs]
    for path in upload:
        print(f"  upload {path.name} ...")
        _gh("release", "upload", tag, str(path), "--clobber", repo=repo, capture=True)
    print("done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
