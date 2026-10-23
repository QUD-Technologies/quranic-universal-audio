#!/usr/bin/env python3
"""Write a synthetic non-Hafs delivery into a bucket, for verification.

No non-Hafs recitation exists yet, so the only way to exercise the multi-riwayah
surfaces end to end is to fabricate one. Everything this writes comes from
``qua_domain`` itself — the edition index supplies the coordinates and the exact
script, the projection supplies the Hafs evidence — so the fixture cannot
disagree with the code under test about what a Warsh word is.

Usage::

    python scripts/devenv/make_synthetic_delivery.py <slug> --riwayah warsh --chapter 112
    python scripts/devenv/make_synthetic_delivery.py <slug> --riwayah qalun --chapter 112 \
        --with-word-shards

The delivery row itself is NOT created here — add it with
``.claude/skills/inspector-admin/scripts/admin_catalog.py`` first, then drive the
lifecycle with ``admin_state.py``. This script only writes bucket content:

===========================================  ==========================================
``reciters/<slug>/detailed.json``            one seg per verse, in the edition's refs
``reciters/<slug>/segments.json``            the verse-aggregated view of the above
``reciters/<slug>/pipeline_meta.json``       the riwayah the "extraction" ran under
``catalog/audio_manifest/<slug>.json``       one chapter pointing at a public CDN mp3
``reciters/<slug>/timestamps/<ch>.json.br``  only with ``--with-word-shards``
===========================================  ==========================================

Deliberately absent: ``low_confidence_v2.json`` and ``ts_validation.json``. Both
are tight-beam probes against Hafs proxy phones and are not produced for another
edition (D12), so their absence is part of what the fixture proves.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "scripts" / "bucket"))

from _bootstrap import add_bucket_args, batch_write, confirm_mutation, resolve  # noqa: E402

from qua_shared.projection_support import support_for  # noqa: E402
from qua_shared.riwayat import (  # noqa: E402
    DEFAULT_SDK_RIWAYAH,
    SUPPORTED_RIWAYAT,
    from_sdk_slug,
)

# A short, public, CORS-reachable chapter file. Anything the audio proxy can
# stream works; the timings below are laid out inside ``--duration-ms``, not
# measured from it, because nothing about this fixture is a real recitation.
DEFAULT_AUDIO_URL = "https://download.quranicaudio.com/qdc/abdul_baset/murattal/{chapter}.mp3"

#: ``("start", "join", "sakt", "stop")`` — the word-shard boundary state codes.
_JOIN = 1
_STOP = 3


def _edition_words(riwayah: str, chapter: int) -> list:
    """Every word of ``chapter`` in ``riwayah``, in recitation order."""
    import qua_domain

    words = [w for w in qua_domain.load_edition_index(riwayah).words if w.surah == chapter]
    if not words:
        raise SystemExit(f"{riwayah} has no surah {chapter}")
    return words


def _verses(words: list) -> list[tuple[int, list]]:
    """``[(ayah, [word, ...]), ...]`` in ayah order."""
    grouped: dict[int, list] = {}
    for word in words:
        grouped.setdefault(word.ayah, []).append(word)
    return sorted(grouped.items())


def _spans(count: int, duration_ms: int, gap_ms: int) -> list[tuple[int, int]]:
    """``count`` equal, non-overlapping spans inside ``duration_ms``.

    A real recitation's spans come from the aligner; these only have to be
    ordered, disjoint and inside the audio, which is what every consumer and the
    shard audit actually check.
    """
    slot = duration_ms // count
    if slot <= gap_ms:
        raise SystemExit(
            f"{duration_ms} ms cannot hold {count} spans with a {gap_ms} ms gap — "
            "raise --duration-ms"
        )
    return [(index * slot, index * slot + slot - gap_ms) for index in range(count)]


def _source_span(projection, first_ref: str, last_ref: str) -> tuple[str, str]:
    """The Hafs span a target verse came from, plus its projection support.

    Recognition and DP matching always run against Hafs, so a non-Hafs seg
    records the source words the matcher would have matched. Support is asked of
    the source side by the shared helper the Inspector's save path uses, so the
    fixture and the real thing cannot disagree.
    """
    first = projection.reverse_ref(first_ref)
    last = projection.reverse_ref(last_ref)
    if not first or not last:
        raise SystemExit(f"{first_ref}..{last_ref} has no Hafs source — projection is incomplete")
    span = f"{first[0]}-{last[-1]}"
    return span, support_for(projection, span)


def _detailed(slug: str, riwayah: str, chapter: int, verses: list, spans: list) -> dict:
    import qua_domain

    projection = qua_domain.load_edition_projection(riwayah, reference_riwayah="hafs")
    segments = []
    for (ayah, words), (start, end) in zip(verses, spans, strict=True):
        first_ref = f"{chapter}:{ayah}:1"
        last_ref = f"{chapter}:{ayah}:{len(words)}"
        source_ref, support = _source_span(projection, first_ref, last_ref)
        segments.append(
            {
                "time_start": start,
                "time_end": end,
                "matched_ref": f"{first_ref}-{last_ref}",
                "confidence": 0.9,
                "source_ref": source_ref,
                "projection_support": support,
            }
        )
    return {
        "_meta": {
            "created_at": datetime.now(UTC).isoformat(),
            "riwayah": from_sdk_slug(riwayah),
            "audio_source": "synthetic",
        },
        "entries": [{"ref": str(chapter), "segments": segments}],
    }


def _word_shard(riwayah: str, chapter: int, verses: list, spans: list) -> dict:
    """A single-reading word-profile shard covering the whole chapter.

    One part per verse, so the parts partition the word list exactly the way the
    audit requires, and the words carry the edition's own text — which is the
    fact the audit pins against ``words_sha256``.
    """
    import qua_domain

    edition = qua_domain.get_edition(riwayah)
    projection = qua_domain.load_edition_projection(riwayah, reference_riwayah="hafs")

    parts: list[list] = []
    rows: list[list] = []
    boundaries: list[list] = []
    for (ayah, words), (verse_start, verse_end) in zip(verses, spans, strict=True):
        parts.append([f"{chapter}:{ayah}", verse_start, verse_end, len(rows), len(words)])
        for index, (word, (start, end)) in enumerate(
            zip(words, _spans(len(words), verse_end - verse_start, 20), strict=True)
        ):
            rows.append([word.ref, word.text, verse_start + start, verse_start + end])
            last = index == len(words) - 1
            boundaries.append([_STOP if last else _JOIN, ayah if last else None])

    return {
        "_meta": {
            "schema_version": 14,
            "profile": "word",
            "chapter": chapter,
            "audio_category": "by_surah",
            "riwayah": riwayah,
            "edition_id": edition.edition_id,
            "words_sha256": edition.words_sha256,
            "timing_provider": "hafs_proxy_mfa",
            "reference_riwayah": projection.reference_riwayah,
            "reference_id": projection.reference_id,
            "projection_id": projection.projection_id,
            "projection_sha256": projection.projection_sha256,
        },
        "readings": [{"id": "r1", "parts": parts, "words": rows, "boundaries": boundaries}],
    }


def _audio_manifest(slug: str, chapter: int, url: str, duration_ms: int) -> dict:
    return {
        "schema_version": 1,
        "slug": slug,
        "_meta": {
            "checksum": hashlib.sha256(url.encode("utf-8")).hexdigest(),
            "chapter_count": 1,
            "category": "by_surah",
        },
        "chapters": {str(chapter): {"url": url, "duration_sec": duration_ms // 1000}},
    }


def _encode(doc: dict) -> bytes:
    return json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("slug", help="delivery slug (the catalog row must already exist)")
    parser.add_argument(
        "--riwayah",
        required=True,
        # Hafs is excluded: its deliveries come from the real pipeline, and a
        # Hafs word-profile shard is a contradiction (Hafs has a native profile
        # with cells, sounds and letter timings). The projection would also be
        # the identity, so the fixture would prove nothing about projection.
        choices=sorted(set(SUPPORTED_RIWAYAT.values()) - {DEFAULT_SDK_RIWAYAH}),
        help="SDK riwayah slug the fixture is written in (non-Hafs)",
    )
    parser.add_argument("--chapter", type=int, default=112)
    parser.add_argument(
        "--with-word-shards",
        action="store_true",
        help="also write the word-profile timestamps shard (Timestamps-tab fixture)",
    )
    parser.add_argument("--audio-url", default=None, help="override the chapter audio URL")
    parser.add_argument("--duration-ms", type=int, default=20_000)
    parser.add_argument("--gap-ms", type=int, default=120, help="silence between verses")
    add_bucket_args(parser)
    args = parser.parse_args()

    try:
        import qua_domain  # noqa: F401
    except ImportError:
        raise SystemExit(
            "qua_domain is not installed — run scripts/devenv/install_qua_domain.py first"
        ) from None

    _, bucket_id = resolve(args)
    confirm_mutation(args, f"write synthetic {args.riwayah} delivery {args.slug}")

    words = _edition_words(args.riwayah, args.chapter)
    verses = _verses(words)
    spans = _spans(len(verses), args.duration_ms, args.gap_ms)
    url = (args.audio_url or DEFAULT_AUDIO_URL).format(chapter=args.chapter)

    detailed = _detailed(args.slug, args.riwayah, args.chapter, verses, spans)

    sys.path.insert(0, str(_REPO_ROOT / "inspector"))
    from adapters.segments_json import build_segments_doc

    files: dict[str, bytes | Path] = {
        f"reciters/{args.slug}/detailed.json": _encode(detailed),
        f"reciters/{args.slug}/segments.json": _encode(
            build_segments_doc(detailed["entries"], {"riwayah": from_sdk_slug(args.riwayah)})
        ),
        f"reciters/{args.slug}/pipeline_meta.json": _encode(
            {
                "schema_version": 1,
                "generated_at": datetime.now(UTC).isoformat(),
                "riwayah": from_sdk_slug(args.riwayah),
            }
        ),
        f"catalog/audio_manifest/{args.slug}.json": _encode(
            _audio_manifest(args.slug, args.chapter, url, args.duration_ms)
        ),
    }

    if args.with_word_shards:
        # Through the real writer, so the audit gates the fixture itself: a
        # fixture the producer's own checks reject would prove nothing.
        from qua_shared.timestamps_shards import validated_brotli_shard

        shard = _word_shard(args.riwayah, args.chapter, verses, spans)
        files[f"reciters/{args.slug}/timestamps/{args.chapter}.json.br"] = validated_brotli_shard(
            shard
        )

    batch_write(bucket_id, files)
    print(f"wrote {len(files)} file(s) for {args.slug} ({args.riwayah}, surah {args.chapter}):")
    for path in files:
        print(f"  {path}")
    print(f"  {len(verses)} verse(s), {len(words)} word(s), audio {url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
