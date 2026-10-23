#!/usr/bin/env python3
"""Rewrite legacy mp3quran chapter urls in audio manifests to their CDN target.

mp3quran moved every moshaf to ``https://cdn.mp3quran.net/audio/<reciter>/rN/<ch>.mp3``
(``rN`` = the reciter's Nth moshaf, not a revision). The old
``https://serverN.mp3quran.net/<dir>/<ch>.mp3`` hosts only 301 there, so every
manifest still on them depends on mp3quran keeping that redirect up, and every
play pays the extra hop.

Each legacy url is resolved from its redirect, never derived: the ``Location`` must
be on ``cdn.mp3quran.net``, keep the file name, and answer 200. A slug with any
chapter that fails is reported and left untouched. Only ``url`` changes; the
chapter's size / duration / bitrate stay as they are (``upstream_drift.py`` audits
those). ``_meta.checksum`` is restamped because it digests the urls.

Dry-run by default: writes the rewritten manifests and a ``report.json`` to
``--out-dir``. ``--apply`` writes them to the bucket in one batch (``--yes-prod``
for prod). A running Space caches manifests, so restart it after a prod apply.

  python scripts/backfills/canonicalize_mp3quran_urls.py --bucket dev
  python scripts/backfills/canonicalize_mp3quran_urls.py --bucket prod --slug raad_al_kurdi_mp3quran
  python scripts/backfills/canonicalize_mp3quran_urls.py --bucket prod --apply --yes-prod
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import copy
import json
import re
import sys
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests

_REPO = Path(__file__).resolve().parents[2]
for _p in (_REPO, _REPO / "inspector", _REPO / "scripts" / "bucket"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import _bootstrap as bs  # noqa: E402

from qua_shared.schemas.bucket.catalog import AudioManifestSidecar  # noqa: E402
from services.audio.audio_meta import manifest_checksum  # noqa: E402

LEGACY_HOST = re.compile(r"^server\d+\.mp3quran\.net$")
CDN_HOST = "cdn.mp3quran.net"
MANIFEST_DIR = "catalog/audio_manifest"
REDIRECTS = (301, 302, 307, 308)
HTTP_TIMEOUT_S = 20
ATTEMPTS = 3
RETRY_BACKOFF_S = 2.0
WORKERS = 16
DEFAULT_OUT_DIR = ".local/mp3quran_cdn_urls"


class ResolveError(RuntimeError):
    """A legacy url did not redirect to a live CDN copy of the same file."""


class _Transient(RuntimeError):
    pass


def is_legacy(url: str) -> bool:
    return bool(LEGACY_HOST.match(urlsplit(url).hostname or ""))


def _head_once(url: str, head: Callable) -> requests.Response:
    try:
        resp = head(url, allow_redirects=False, timeout=HTTP_TIMEOUT_S)
    except requests.RequestException as e:
        raise _Transient(f"{type(e).__name__}: {e}") from e
    if resp.status_code == 429 or resp.status_code >= 500:
        raise _Transient(f"http {resp.status_code}")
    return resp


def _head(url: str, head: Callable) -> requests.Response:
    for attempt in range(ATTEMPTS):
        try:
            return _head_once(url, head)
        except _Transient as e:
            if attempt == ATTEMPTS - 1:
                raise ResolveError(f"{url}: {e}") from e
            time.sleep(RETRY_BACKOFF_S * (attempt + 1))
    raise AssertionError("unreachable")


def resolve_redirect(url: str, *, head: Callable = requests.head) -> str:
    """The CDN url ``url`` redirects to, checked to be the same file and live."""
    resp = _head(url, head)
    location = resp.headers.get("location")
    if resp.status_code not in REDIRECTS or not location:
        raise ResolveError(f"{url}: no redirect (http {resp.status_code})")
    target = urljoin(url, location)
    if urlsplit(target).hostname != CDN_HOST:
        raise ResolveError(f"{url}: unexpected host in {target}")
    if urlsplit(target).path.rsplit("/", 1)[-1] != urlsplit(url).path.rsplit("/", 1)[-1]:
        raise ResolveError(f"{url}: file name changed in {target}")
    status = _head(target, head).status_code
    if status != 200:
        raise ResolveError(f"{url}: target answered {status} ({target})")
    return target


def rewrite_manifest(
    doc: dict, resolve: Callable[[str], str]
) -> tuple[dict, list[tuple[str, str, str]]]:
    """A copy of ``doc`` with every legacy chapter ``url`` resolved, plus the
    ``(chapter, old, new)`` rewrites. ``doc`` itself is not modified."""
    new = copy.deepcopy(doc)
    rewrites = []
    for key, entry in (new.get("chapters") or {}).items():
        url = entry.get("url") or ""
        if is_legacy(url):
            entry["url"] = resolve(url)
            rewrites.append((key, url, entry["url"]))
    if rewrites:
        new.setdefault("_meta", {})["checksum"] = manifest_checksum(new["chapters"])
    return new, rewrites


# --- bucket I/O ------------------------------------------------------------------


def _manifest_rel(slug: str) -> str:
    return f"{MANIFEST_DIR}/{slug}.json"


def _list_slugs(fs, bucket: str) -> list[str]:
    names = fs.ls(bs.abs_path(bucket, MANIFEST_DIR), detail=False)
    return sorted(n.rsplit("/", 1)[-1][: -len(".json")] for n in names if n.endswith(".json"))


def _read_manifests(fs, bucket: str, slugs: list[str]) -> tuple[dict[str, dict], dict[str, str]]:
    def read(slug):
        try:
            return slug, json.loads(bs.rl(fs.cat_file, bs.abs_path(bucket, _manifest_rel(slug))))
        except Exception as e:  # noqa: BLE001 — reported per slug
            return slug, e

    docs, errors = {}, {}
    with cf.ThreadPoolExecutor(WORKERS) as ex:
        for slug, out in ex.map(read, slugs):
            if isinstance(out, dict):
                docs[slug] = out
            else:
                errors[slug] = f"manifest read: {type(out).__name__}: {out}"
    return docs, errors


def _resolve_all(urls: set[str]) -> dict[str, str | ResolveError]:
    def resolve(url):
        try:
            return url, resolve_redirect(url)
        except ResolveError as e:
            return url, e

    with cf.ThreadPoolExecutor(WORKERS) as ex:
        return dict(ex.map(resolve, sorted(urls)))


def _legacy_urls(doc: dict) -> set[str]:
    return {e.get("url") for e in (doc.get("chapters") or {}).values() if is_legacy(e.get("url") or "")}


def _rewrite_all(docs: dict[str, dict], resolved: dict) -> tuple[dict, dict, dict]:
    """``(rewritten docs, per-slug rewrites, per-slug failures)``."""
    out, rewrites, failures = {}, {}, {}
    for slug, doc in docs.items():
        bad = sorted(str(resolved[u]) for u in _legacy_urls(doc) if isinstance(resolved[u], ResolveError))
        if bad:
            failures[slug] = bad
            continue
        new, changes = rewrite_manifest(doc, resolved.__getitem__)
        try:
            AudioManifestSidecar.model_validate(new)
        except Exception as e:  # noqa: BLE001 — reported per slug
            failures[slug] = [f"schema: {e}"]
            continue
        out[slug], rewrites[slug] = new, changes
    return out, rewrites, failures


def _prefixes(changes: list[tuple[str, str, str]]) -> list[str]:
    """Distinct ``old dir → new dir`` pairs, the per-slug summary line."""
    pairs = {(old.rsplit("/", 1)[0], new.rsplit("/", 1)[0]) for _, old, new in changes}
    return sorted(f"{a}/ → {b}/" for a, b in pairs)


def _report(rewrites: dict, failures: dict, read_errors: dict) -> dict:
    return {
        "slugs_rewritten": len(rewrites),
        "chapters_rewritten": sum(len(c) for c in rewrites.values()),
        "slugs_failed": len(failures) + len(read_errors),
        "rewrites": {s: {"chapters": len(c), "dirs": _prefixes(c)} for s, c in sorted(rewrites.items())},
        "failures": {**failures, **{s: [e] for s, e in read_errors.items()}},
    }


def _encode(doc: dict) -> bytes:
    return (json.dumps(doc, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--slug", action="append", default=[], help="slug (repeatable; default: every manifest)")
    p.add_argument("--out-dir", default=DEFAULT_OUT_DIR, help="dry-run manifests + report.json")
    p.add_argument("--apply", action="store_true", help="write the rewritten manifests to the bucket")
    bs.add_bucket_args(p)
    a = p.parse_args()

    fs, bucket = bs.resolve(a)
    slugs = a.slug or _list_slugs(fs, bucket)
    docs, read_errors = _read_manifests(fs, bucket, slugs)
    docs = {s: d for s, d in docs.items() if _legacy_urls(d)}
    urls = set().union(*(_legacy_urls(d) for d in docs.values())) if docs else set()
    print(f"{bucket}: {len(slugs)} manifests, {len(docs)} on legacy hosts, {len(urls)} urls", file=sys.stderr)

    rewritten, rewrites, failures = _rewrite_all(docs, _resolve_all(urls))
    report = _report(rewrites, failures, read_errors)
    out_dir = Path(a.out_dir) / a.bucket
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    for slug, f in report["failures"].items():
        print(f"  FAILED {slug}: {f[0]}" + (f" (+{len(f) - 1} more)" if len(f) > 1 else ""))
    print(
        f"{report['chapters_rewritten']} chapters in {report['slugs_rewritten']} manifests to rewrite; "
        f"{report['slugs_failed']} failed — report: {out_dir / 'report.json'}"
    )

    files = {_manifest_rel(s): _encode(d) for s, d in rewritten.items()}
    if a.apply and files:
        bs.confirm_mutation(a, f"write {len(files)} manifests")
        bs.batch_write(bucket, files)
        print(f"wrote {len(files)} manifests to {bucket}")
    else:
        for rel, body in files.items():
            (out_dir / rel.rsplit("/", 1)[-1]).write_bytes(body)
    return 1 if report["slugs_failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
