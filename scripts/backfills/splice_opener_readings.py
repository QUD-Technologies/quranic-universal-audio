"""Splice each chapter's recorded openers into its existing timestamps shard.

A shard is a list of readings, and an opener (Isti'adha / Basmala before ayah 1)
is a reading of its own. So a chapter whose verses are already timed does not
need a full regeneration to show its openers: one aligner call over the openers
alone (``specials: true``, no verse segments) times them and builds their
readings, which go in front of the shard's verse readings, ids renumbered as a
full run would number them. The shard's ``_meta`` stays as it was; openers carry
no madd lāzim, so the delivery's variant basis does not change them (checked:
identical readings with and without it). Each opener's word times are written
back to ``specials.json``, and each reciter that changed is reported through
``POST /api/admin/internal/ts-refreshed`` so its releases go stale.

Skips by-ayah deliveries (no lead-in to time), chapters with no shard, and
chapters whose shard already has opener readings, so a rerun resumes.

Dry run by default. Needs ``HF_TOKEN``, ``INSPECTOR_EXTRACTION_SECRET`` and
``INSPECTOR_WEBHOOK_SECRET``.

Examples::

    python scripts/backfills/splice_opener_readings.py --slug-file slugs.txt --bucket prod
    python scripts/backfills/splice_opener_readings.py --slug-file slugs.txt --bucket prod --execute --workers 6
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

log = logging.getLogger("splice_opener_readings")

_BUCKETS = {
    "dev": "QUD-Technologies/quranic-inspector-bucket-dev",
    "prod": "QUD-Technologies/quranic-inspector-bucket",
}
_INSPECTORS = {
    "dev": "https://qud-technologies-quranic-inspector-dev.hf.space",
    "prod": "https://qud-technologies-quranic-universal-audio.hf.space",
}
#: The aligner both Inspector Spaces time against (their ``INSPECTOR_ALIGNER_URL``).
ALIGNER_URL = "https://qud-technologies-quranic-universal-aligner-dev.hf.space"


def splice(shard: dict, openers: dict) -> dict:
    """``shard`` with its opener readings replaced by ``openers``' and ids renumbered."""
    verses = [r for r in shard["readings"] if not r.get("special")]
    added = [r for r in openers["readings"] if r.get("special")]
    readings = [{**r, "id": f"r{i}"} for i, r in enumerate(added + verses, start=1)]
    return {**shard, "readings": readings}


def has_openers(shard: dict) -> bool:
    return any(r.get("special") for r in shard["readings"])


def _setup_paths_and_env(bucket: str) -> None:
    """Import the Inspector against ``bucket`` with a private, never-uploaded copy of its DB."""
    repo = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repo / "inspector"))
    sys.path.insert(0, str(repo))
    os.environ["INSPECTOR_BUCKET_REPO"] = _BUCKETS[bucket]
    if bucket == "prod":
        os.environ["INSPECTOR_ALLOW_PROD_BUCKET"] = "1"
    os.environ.setdefault("INSPECTOR_ALIGNER_URL", ALIGNER_URL)
    os.environ["INSPECTOR_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "inspector.db")

    from services.db import sync

    def refuse(*_args, **_kwargs):
        raise RuntimeError("backfills never upload the state DB")

    sync.upload = refuse
    sync.daily_snapshot = refuse
    sync.pull()


class _Splicer:
    """One reciter's chapters: time their openers and splice them into the shards."""

    def __init__(self, slug: str, execute: bool):
        import brotli

        from services.reference.delivery_edition import sdk_riwayah_for
        from services.storage import storage_paths
        from services.storage.hf_bucket import get_backend
        from services.timing import aligner_timing as at
        from services.timing import specials_timing

        self.slug, self.execute = slug, execute
        self.brotli, self.paths, self.backend = brotli, storage_paths, get_backend()
        self.at, self.timing = at, specials_timing
        self.detailed = at.read_detailed(slug)
        self.riwayah = sdk_riwayah_for(slug)
        #: Opener word times per spliced chapter, written to specials.json once at the end.
        self.words: dict[int, dict] = {}

    def by_surah(self) -> bool:
        return self.at.audio_category(self.detailed) == "by_surah_audio"

    def chapters(self) -> list[int]:
        doc = self.timing.specials_service.read_doc(self.slug)
        return sorted(int(c) for c in (doc.chapters if doc else {}))

    def _shard(self, chapter: int) -> dict | None:
        path = self.paths.timestamps_path_br(self.slug, chapter)
        if not self.backend.exists(path):
            return None
        return json.loads(self.brotli.decompress(self.backend.read_bytes(path)))

    def run(self, chapter: int) -> str:
        openers = self.timing.opener_segments(self.slug, chapter)
        if not openers:
            return "no_openers"
        shard = self._shard(chapter)
        if shard is None:
            return "no_shard"
        if has_openers(shard):
            return "present"
        if not self.execute:
            return "would_splice"
        reply = self._time(chapter, openers)
        if reply["failed_segments"] or reply.get("untimed"):
            log.error(
                "%s ch%d: openers not timed: %s", self.slug, chapter, reply["failed_segments"]
            )
            return "failed"
        built = json.loads(self.brotli.decompress(base64.b64decode(reply["shards"][str(chapter)])))
        out = json.dumps(splice(shard, built), ensure_ascii=False, separators=(",", ":"))
        self.backend.write_bytes_atomic(
            self.paths.timestamps_path_br(self.slug, chapter), self.brotli.compress(out.encode())
        )
        self.words[chapter] = self.timing.opener_words(openers, base64.b64decode(reply["times"]))
        return "spliced"

    def _time(self, chapter: int, openers: list[dict]) -> dict:
        at = self.at
        entries = at.entries_by_chapter(self.detailed)[chapter]
        times = at.read_times(self.slug, chapter)
        bare = [{**e, "segments": []} for e in entries]
        body = {
            "slug": self.slug,
            "chapter": chapter,
            "riwayah": self.riwayah,
            "audio_category": at.audio_category(self.detailed),
            "entries": self.timing.with_openers(bare, openers),
            "audio_refs": {str(e["ref"]): at._audio_ref(self.slug, e["ref"]) for e in entries},
            "frame_refs": {str(e["ref"]): at._frames_ref(self.slug, e["ref"]) for e in entries},
            "times": base64.b64encode(times).decode() if times else None,
            "full": False,
            "shards": True,
            "delivery_lazim_ms": [],
            "specials": True,
            # A one-off backfill: keep it off the ZeroGPU quota live alignments use.
            "device": "CPU",
        }
        return at._post(body, f"{self.slug} ch{chapter} openers")


def _run_slug(slug: str, execute: bool, workers: int, notify) -> dict[str, int]:
    splicer = _Splicer(slug, execute)
    if not splicer.by_surah():
        log.info("%s: by-ayah audio, skipped", slug)
        return {"by_ayah": 1}
    counts: dict[str, int] = {}

    def one(chapter: int) -> str:
        try:
            return splicer.run(chapter)
        except Exception:
            log.exception("%s ch%d: splice failed", slug, chapter)
            return "failed"

    with ThreadPoolExecutor(max(1, workers)) as pool:
        for outcome in pool.map(one, splicer.chapters()):
            counts[outcome] = counts.get(outcome, 0) + 1
    splicer.timing.write_words(slug, splicer.words)
    if counts.get("spliced") and not notify(slug):
        log.error("%s: spliced but the ts-refreshed report was not accepted", slug)
        counts["unreported"] = 1
    log.info("%s: %s", slug, counts)
    return counts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--slug", nargs="+")
    ap.add_argument("--slug-file", help="whitespace-separated slugs")
    ap.add_argument("--bucket", choices=sorted(_BUCKETS), default="prod")
    ap.add_argument("--workers", type=int, default=4, help="chapters timed at once")
    ap.add_argument("--execute", action="store_true", help="Write. Without it only count.")
    args = ap.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    for noisy in ("httpx", "httpx2"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    slugs = list(args.slug or [])
    if args.slug_file:
        slugs += Path(args.slug_file).read_text("utf-8").split()
    if not slugs:
        ap.error("no slugs given")
    _setup_paths_and_env(args.bucket)

    from qua_shared.inspector_notify import notify_ts_refreshed

    def notify(slug: str) -> bool:
        return notify_ts_refreshed(_INSPECTORS[args.bucket], slug, reason="opener_splice")

    total: dict[str, int] = {}
    for slug in dict.fromkeys(slugs):
        for k, v in _run_slug(slug, args.execute, args.workers, notify).items():
            total[k] = total.get(k, 0) + v
    log.info("%s: %s", "EXECUTED" if args.execute else "DRY RUN", total)
    return 1 if total.get("failed") or total.get("unreported") else 0


if __name__ == "__main__":
    raise SystemExit(main())
