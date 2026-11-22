"""Backfill / correct per-chapter audio metadata in audio-manifest sidecars.

Fills null ``duration_sec`` / ``bitrate_kbps`` / ``bitrate_mode`` and (with
``--fix-drift``) corrects phantom-tail durations, choosing the most authoritative
source available per chapter — never a bitrate-estimate against the wrong size:

  duration_sec  peaks header ``duration_ms`` (ffmpeg-decoded, dead-tail-free) when
                peaks exist > decoded bucket mp3 > source-URL probe.
  bitrate/mode  decoded bucket mp3 (what's actually served) > source-URL probe.

Three populations this covers (all by_surah; by_ayah skipped):
  * extracted-but-unprobed (bucket audio + peaks, null meta) — e.g. ayman_swed_muallim_tvquran
  * never-extracted / CDN stream-through (null meta, no bucket bytes) — ~60 reciters,
    metadata recovered by probing the source CDN url (archive.org/mp3quran/tvquran).
  * phantom-tail (non-null but wrong duration; manifest longer than the decoded
    peaks length) — e.g. maher_al_muaiqly_mp3quran ch76; fixed from the peaks length.

Bucket audio / peaks are read from a local hf-mount when ``--mount`` is given
(no bucket API calls); source urls are HTTP range-probed (ID3v2 cover-art tags
skipped, so a ~256 KB audio-region fetch suffices for CBR/Xing — the common case).

Dry-run by default (writes the corrected sidecars to ``--out-dir`` for review +
prints a diff). ``--apply`` writes back to the bucket (``--yes-prod`` for prod).

  # one slug, dry-run
  python3.11 scripts/backfills/backfill_audio_manifest_meta.py \
      --slug ayman_swed_muallim_tvquran --bucket prod --mount <mount>

  # all null-metadata reciters from a sweep, dry-run
  python3.11 scripts/backfills/backfill_audio_manifest_meta.py \
      --all-null .local/audio_audit/null_metadata_sweep_prod.json \
      --bucket prod --mount <mount>

  # fix phantom-tail durations from peaks
  python3.11 scripts/backfills/backfill_audio_manifest_meta.py \
      --slug maher_al_muaiqly_mp3quran --fix-drift --drift-pct 8 \
      --bucket prod --mount <mount>
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import gzip
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bucket"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / ".local" / "extraction"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bootstrap as bs  # noqa: E402

from qua_shared.mp3_probe import probe_source  # noqa: E402

META_FIELDS = ("duration_sec", "bitrate_kbps", "bitrate_mode")


# --- bucket-side reads (via mount when available) ----------------------------


def _bucket_audio_bytes(mount: str, fs, bucket: str, slug: str, ch: str) -> bytes | None:
    if mount:
        p = Path(mount) / "reciters" / slug / "audio" / f"{ch}.mp3"
        return p.read_bytes() if p.is_file() else None
    path = bs.abs_path(bucket, f"reciters/{slug}/audio/{ch}.mp3")
    try:
        return fs.cat_file(path)
    except Exception:  # noqa: BLE001
        return None


def _peaks_duration_ms(mount: str, fs, bucket: str, slug: str, ch: str) -> int | None:
    try:
        if mount:
            p = Path(mount) / "reciters" / slug / "peaks" / f"{ch}.json.gz"
            if not p.is_file():
                return None
            blob = gzip.decompress(p.read_bytes())
        else:
            blob = gzip.decompress(
                fs.cat_file(bs.abs_path(bucket, f"reciters/{slug}/peaks/{ch}.json.gz"))
            )
        d = json.loads(blob).get("duration_ms")
        return int(d) if isinstance(d, (int, float)) and d > 0 else None
    except Exception:  # noqa: BLE001
        return None


def _decode_bucket_mp3(data: bytes) -> dict | None:
    """Decoded duration + bitrate + cbr/vbr from bucket mp3 bytes."""
    from remux_bucket_audio import probe

    from qua_shared.mp3_frames import classify_bitrate_mode

    pr = probe(data)
    if pr.error or pr.declared_kbps is None or pr.real_dur_s <= 0:
        return None
    # Canonical verdict: whole-file linear-seek error (NOT len(set(bitrates))==1,
    # which a single stray frame in otherwise-CBR audio flips to a false 'vbr').
    return {
        "duration_sec": pr.real_dur_s,
        "bitrate_kbps": int(pr.declared_kbps),
        "bitrate_mode": classify_bitrate_mode(data),
        "sample_rate": pr.sr,
    }


# --- per-chapter resolution --------------------------------------------------


def resolve_chapter(args, fs, bucket, slug, ch, entry, drift_pct):
    """Return (new_entry, note) — null fields filled, optionally drift-fixed."""
    cur = {f: entry.get(f) for f in META_FIELDS}
    need_meta = cur["bitrate_kbps"] is None or cur["bitrate_mode"] is None
    need_dur = cur["duration_sec"] is None

    peaks_ms = _peaks_duration_ms(args.mount, fs, bucket, slug, ch)
    bdec = None
    audio = _bucket_audio_bytes(args.mount, fs, bucket, slug, ch)
    if audio is not None and (need_meta or (need_dur and peaks_ms is None)):
        bdec = _decode_bucket_mp3(audio)

    src = None
    src_needed = audio is None and (need_meta or (need_dur and peaks_ms is None))
    if src_needed and entry.get("url"):
        src = probe_source(entry["url"], allow_full=args.allow_full)

    method = []
    new = dict(entry)

    # Stale archive.org node url repaired to its canonical form (also fixes playback).
    if (
        args.rewrite_urls
        and src
        and not src.error
        and src.resolved_url
        and src.resolved_url != entry.get("url")
    ):
        new["url"] = src.resolved_url
        method.append("url:canonical")

    # bitrate / mode
    if cur["bitrate_kbps"] is None:
        if bdec:
            new["bitrate_kbps"] = bdec["bitrate_kbps"]
            method.append("kbps:bucket")
        elif src and src.bitrate_kbps:
            new["bitrate_kbps"] = src.bitrate_kbps
            method.append("kbps:source")
    if cur["bitrate_mode"] is None:
        if bdec:
            new["bitrate_mode"] = bdec["bitrate_mode"]
            method.append("mode:bucket")
        elif src and src.bitrate_mode:
            new["bitrate_mode"] = src.bitrate_mode
            method.append("mode:source")

    # duration: prefer peaks (decoded, dead-tail-free)
    dur = cur["duration_sec"]
    if dur is None:
        if peaks_ms is not None:
            dur = peaks_ms / 1000.0
            method.append("dur:peaks")
        elif bdec:
            dur = bdec["duration_sec"]
            method.append("dur:bucket")
        elif src and src.duration_sec:
            dur = src.duration_sec
            method.append(f"dur:source:{src.method}")
    elif args.fix_drift and peaks_ms is not None:
        pk = peaks_ms / 1000.0
        if pk > 0 and abs(dur - pk) / pk * 100.0 >= drift_pct:
            method.append(f"dur:drift-fix({dur}->{round(pk)})")
            dur = pk
    if dur is not None:
        new["duration_sec"] = int(round(dur))

    # Sample rate, captured opportunistically from whichever probe ran (for the
    # delivery-row rollup; not stored per-chapter in the sidecar).
    sr = None
    if bdec:
        sr = bdec.get("sample_rate")
    elif src:
        sr = src.sample_rate

    # Classify the outcome for reporting.
    note = None
    still_null = any(new.get(f) is None for f in META_FIELDS)
    if src and src.method == "needs_full":
        note = "needs_full"  # headerless VBR; rerun with --allow-full
    elif src and src.error:
        note = f"source-error:{src.error}"
    elif still_null:
        note = "unresolved"
    return new, method, note, sr


def process_slug(args, fs, bucket, slug, drift_pct):
    man_rel = f"catalog/audio_manifest/{slug}.json"
    try:
        if args.mount:
            raw = (Path(args.mount) / man_rel).read_bytes()
        else:
            raw = fs.cat_file(bs.abs_path(bucket, man_rel))
    except Exception as e:  # noqa: BLE001
        return {"slug": slug, "error": f"manifest-read:{e!r}"}
    sidecar = json.loads(raw)
    chapters = sidecar.get("chapters") or {}
    keys = sorted((k for k in chapters if ":" not in k and k.isdigit()), key=int)

    dead = set(args.dead_map.get(slug, [])) if args.dead_map else set()

    changed = 0
    url_rewrites = 0
    needs_full: list[str] = []
    src_errors: list[str] = []
    unresolved: list[str] = []
    sample: list[str] = []
    sample_rates: dict[int, int] = {}
    for ch in keys:
        if ch in dead:
            continue  # removed below; don't probe a known-dead url
        entry = dict(chapters[ch] or {})
        needs_null = any(entry.get(f) is None for f in META_FIELDS)
        if not needs_null and not args.fix_drift:
            continue
        new, method, note, sr = resolve_chapter(args, fs, bucket, slug, ch, entry, drift_pct)
        if sr:
            sample_rates[sr] = sample_rates.get(sr, 0) + 1
        if new.get("url") != entry.get("url"):
            url_rewrites += 1
        if note == "needs_full":
            needs_full.append(ch)
        elif note and note.startswith("source-error"):
            src_errors.append(ch)
        elif note == "unresolved":
            unresolved.append(ch)
        meta_changed = {f: new.get(f) for f in META_FIELDS} != {
            f: entry.get(f) for f in META_FIELDS
        }
        if meta_changed or new.get("url") != entry.get("url"):
            chapters[ch] = new
            changed += 1
            if len(sample) < 4:
                sample.append(
                    f"ch{ch} {{{','.join(method)}}}: "
                    + ", ".join(f"{f}={new.get(f)}" for f in META_FIELDS)
                )

    # Remove dead chapters from the sidecar + _meta.chapter_count.
    removed = [ch for ch in dead if ch in chapters]
    for ch in removed:
        del chapters[ch]

    # URLs changed or chapters removed → recompute _meta.checksum exactly as
    # services/admin/intake.py does (sorted "key=url;" pairs, sha256[:16]).
    if url_rewrites or removed:
        import hashlib

        sidecar.setdefault("_meta", {})
        digest = "".join(
            f"{k}={chapters[k]['url']};" for k in sorted(chapters) if chapters[k].get("url")
        ).encode("utf-8")
        sidecar["_meta"]["checksum"] = hashlib.sha256(digest).hexdigest()[:16]
        sidecar["_meta"]["chapter_count"] = sum(1 for k in chapters if ":" not in k and k.isdigit())

    # Delivery-row rollup from the corrected manifest (coverage + encoder fields).
    live = [chapters[k] for k in chapters if ":" not in k and k.isdigit()]
    durs = [e["duration_sec"] for e in live if isinstance(e.get("duration_sec"), (int, float))]
    # Only roll up a total when EVERY live chapter has a duration — a partial sum
    # (some chapters still null from probe failures) would undercount coverage.
    dur_complete = bool(live) and len(durs) == len(live)
    modes = {e.get("bitrate_mode") for e in live if e.get("bitrate_mode")}
    kbpss = [e["bitrate_kbps"] for e in live if isinstance(e.get("bitrate_kbps"), (int, float))]
    if not modes:
        roll_mode = None
    elif modes == {"cbr"}:
        roll_mode = "cbr"
    elif modes == {"vbr"}:
        roll_mode = "vbr"
    else:
        roll_mode = "mixed"
    if roll_mode == "mixed":
        roll_nominal = None  # model validator forbids a nominal on mixed
    elif kbpss:
        roll_nominal = (
            round(sum(kbpss) / len(kbpss))
            if roll_mode == "vbr"
            else max(set(kbpss), key=kbpss.count)
        )
    else:
        roll_nominal = None
    rollup = {
        "chapter_count": len(live),
        "total_duration_sec": int(round(sum(durs))) if dur_complete else None,
        "bitrate_mode": roll_mode,
        "bitrate_kbps_nominal": roll_nominal,
        "sample_rate_hz": max(sample_rates, key=sample_rates.get) if sample_rates else None,
    }

    return {
        "slug": slug,
        "n_chapters": len(keys),
        "changed": changed,
        "url_rewrites": url_rewrites,
        "removed": removed,
        "needs_full": needs_full,
        "src_errors": src_errors,
        "unresolved": unresolved,
        "sample": sample,
        "rollup": rollup,
        "sidecar": sidecar,
        "man_rel": man_rel,
    }


def _validate(sidecar) -> str | None:
    try:
        repo = str(Path(__file__).resolve().parents[2])
        if repo not in sys.path:
            sys.path.insert(0, repo)
        from qua_shared.schemas.bucket.catalog import AudioManifestSidecar

        AudioManifestSidecar.model_validate(sidecar)
        return None
    except Exception as e:  # noqa: BLE001
        return repr(e)


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--slug", action="append", default=[], help="slug (repeatable)")
    p.add_argument(
        "--all-null", default="", help="path to a sweep JSON; process every null_metadata slug"
    )
    p.add_argument("--mount", default="", help="local hf-mount root (bucket reads, no API)")
    p.add_argument(
        "--fix-drift", action="store_true", help="also overwrite duration from peaks when it drifts"
    )
    p.add_argument("--drift-pct", type=float, default=8.0)
    p.add_argument(
        "--allow-full",
        action="store_true",
        help="download whole file for headerless-VBR duration (slow; default flags them needs_full)",
    )
    p.add_argument(
        "--rewrite-urls",
        action="store_true",
        help="repair stale archive.org node urls to canonical archive.org/download form "
        "(fixes playback too) and recompute _meta.checksum",
    )
    p.add_argument(
        "--remove-dead",
        default="",
        help="path to url_audit JSON; drop each reciter's dead_chapters + recompute count/checksum",
    )
    p.add_argument("--workers", type=int, default=8)
    p.add_argument(
        "--out-dir",
        default="/tmp/manifest_backfill",
        help="where dry-run corrected sidecars are written",
    )
    p.add_argument("--sql-out", default="", help="write delivery-row rollup UPDATE statements here")
    p.add_argument("--apply", action="store_true", help="write corrected sidecars to the bucket")
    bs.add_bucket_args(p)
    a = p.parse_args()

    a.dead_map = {}
    if a.remove_dead:
        doc = json.load(open(a.remove_dead))
        for r in doc.get("per_reciter", doc.get("reciters", [])):
            if r.get("dead_chapters"):
                a.dead_map[r["slug"]] = [str(c) for c in r["dead_chapters"]]

    fs, bucket = bs.resolve(a)
    slugs = list(a.slug)
    if a.all_null:
        nm = json.load(open(a.all_null))["null_metadata"]
        slugs += [r["slug"] for r in nm if r["slug"] not in slugs]
    if not slugs:
        print("no slugs (pass --slug or --all-null)", file=sys.stderr)
        return 2
    print(f"processing {len(slugs)} slug(s); bucket={bucket} apply={a.apply}", file=sys.stderr)

    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    def work(slug):
        return process_slug(a, fs, bucket, slug, a.drift_pct)

    results = []
    with cf.ThreadPoolExecutor(max_workers=a.workers) as ex:
        for i, r in enumerate(ex.map(work, slugs), 1):
            results.append(r)
            if r.get("error"):
                tail = r["error"]
            else:
                tail = (
                    f"changed={r['changed']}/{r['n_chapters']} "
                    f"url_fix={r['url_rewrites']} "
                    f"needs_full={len(r['needs_full'])} src_err={len(r['src_errors'])} "
                    f"unresolved={len(r['unresolved'])}"
                )
            print(f"  [{i}/{len(slugs)}] {r['slug']}: {tail}", file=sys.stderr)

    ok = [r for r in results if not r.get("error")]
    total_changed = sum(r["changed"] for r in ok)
    total_urlfix = sum(r["url_rewrites"] for r in ok)
    total_full = sum(len(r["needs_full"]) for r in ok)
    total_serr = sum(len(r["src_errors"]) for r in ok)
    total_unres = sum(len(r["unresolved"]) for r in ok)
    errs = [r for r in results if r.get("error")]
    print(
        f"\n==== {total_changed} chapters changed across {len(slugs)} slugs | "
        f"url-rewrites={total_urlfix} | needs_full(headerless-VBR)={total_full} | "
        f"source-errors={total_serr} | unresolved={total_unres} | manifest-errors={len(errs)} ===="
    )
    for r in results:
        if r.get("error"):
            print(f"  ERROR {r['slug']}: {r['error']}")
            continue
        if not r["changed"] and not r["needs_full"] and not r["src_errors"] and not r["unresolved"]:
            continue
        flags = []
        if r["needs_full"]:
            flags.append(f"needs_full={r['needs_full'][:8]}")
        if r["src_errors"]:
            flags.append(f"src_err={r['src_errors'][:8]}")
        if r["unresolved"]:
            flags.append(f"unresolved={r['unresolved'][:8]}")
        print(
            f"\n{r['slug']}: {r['changed']}/{r['n_chapters']} changed"
            + ("  " + " ".join(flags) if flags else "")
        )
        for s in r["sample"]:
            print(f"    {s}")

    total_removed = sum(len(r.get("removed", [])) for r in ok)
    if a.dead_map:
        print(f"dead chapters removed: {total_removed}")

    # Validate + emit/apply manifests.
    bad = 0
    written = []
    to_write: dict[str, bytes] = {}
    for r in results:
        if r.get("error"):
            continue
        touched = r["changed"] or r.get("removed")
        if not touched:
            continue
        v = _validate(r["sidecar"])
        if v:
            print(f"  SCHEMA FAIL {r['slug']}: {v}", file=sys.stderr)
            bad += 1
            continue
        written.append(r)
        body = json.dumps(r["sidecar"], ensure_ascii=False, indent=2)
        if a.apply:
            to_write[r["man_rel"]] = body.encode("utf-8")
        else:
            (out_dir / f"{r['slug']}.json").write_text(body, encoding="utf-8")

    # Apply every manifest in ONE Xet batch (paths, not bytes) instead of a
    # commit per reciter.
    if to_write:
        bs.confirm_mutation(a, f"write {len(to_write)} manifests")
        bs.batch_write(bucket, to_write)

    if bad:
        print(f"\n{bad} slugs failed schema validation — not written", file=sys.stderr)
        return 1

    # Delivery-row rollup UPDATE statements (apply separately via admin_db.py --write).
    def sqlstr(v):
        return "NULL" if v is None else (f"'{v}'" if isinstance(v, str) else str(v))

    sql_lines = []
    for r in written:
        ru = r["rollup"]
        sets = ", ".join(
            f"{col} = {sqlstr(ru[col])}"
            for col in (
                "chapter_count",
                "total_duration_sec",
                "bitrate_mode",
                "bitrate_kbps_nominal",
                "sample_rate_hz",
            )
            if ru.get(col) is not None
        )
        if sets:
            sql_lines.append(f"UPDATE deliveries SET {sets} WHERE slug = '{r['slug']}';")
    if a.sql_out and sql_lines:
        Path(a.sql_out).write_text("\n".join(sql_lines) + "\n", encoding="utf-8")
        print(f"\n{len(sql_lines)} delivery-row UPDATE statements -> {a.sql_out}")

    if a.apply:
        print(
            f"\nAPPLIED {len(written)} manifests to {bucket}. "
            f"Run the {a.sql_out or '--sql-out'} UPDATEs via admin_db.py, then restart the prod Space."
        )
    else:
        print(
            f"\ndry-run — {len(written)} corrected sidecars in {out_dir}/. "
            f"Re-run with --apply (--yes-prod for prod)."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
