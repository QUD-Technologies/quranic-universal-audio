"""Bulk-reverse ``ignore_issue`` ops for a given category on a reciter.

Use case: bring back accidentally-ignored validation issues into the relevant
accordion (e.g. ``cross_verse``) without re-running the full inspector UI 25
times. Drives the existing :func:`services.segments.undo.undo_ops` so the
revert path matches a real admin clicking undo — full audit trail, cache
eviction, schema-correct ``revert`` records appended to ``edit_history.jsonl``.

Selection criteria (must satisfy all):

1. Segment is currently ignored for ``--category`` in ``detailed.json`` —
   ``ignored_categories`` contains the category (or ``_all``), OR
   ``ignored: true`` (legacy global ignore).
2. ``edit_history.jsonl`` contains an ``op_type: "ignore_issue"`` op whose
   ``targets_before`` has no ignore marker for this category AND whose
   ``targets_after`` adds it — the canonical origin of the current ignore
   state.
3. The op hasn't already been reverted (``undo_ops`` enforces this; we
   skip pre-emptively too).

Dry run by default. Pass ``--apply`` to mutate.

Examples::

    python3 scripts/backfills/unignore_category.py \
        --slug ahmed_saud_mp3quran --category cross_verse --bucket prod
    python3 scripts/backfills/unignore_category.py \
        --slug ahmed_saud_mp3quran --category cross_verse --bucket prod --apply
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections import defaultdict
from datetime import UTC
from pathlib import Path

log = logging.getLogger("unignore_category")

_BUCKETS = {
    "dev": "QUD-Technologies/quranic-inspector-bucket-dev",
    "prod": "QUD-Technologies/quranic-inspector-bucket",
}

# Owner identity baked in — this script is owner-only by intent. The audit
# trail will attribute every revert to this user. Sourced from
# access/inspector_roles.json (the only ``owner`` entry today).
_OWNER_ACTOR = {
    "hf_user_id": "684abe5b6327ae8863d106d2",
    "login_at_time": "hetchyy",
    "role": "owner",
}


def _setup_paths_and_env(bucket: str) -> None:
    here = Path(__file__).resolve()
    repo = here.parents[2]
    inspector = repo / "inspector"
    sys.path.insert(0, str(inspector))
    sys.path.insert(0, str(repo))
    os.environ["INSPECTOR_BUCKET_REPO"] = _BUCKETS[bucket]


def _is_ignored_for(seg: dict, category: str) -> bool:
    if seg.get("ignored"):
        return True
    cats = seg.get("ignored_categories") or []
    return category in cats or "_all" in cats


def _snap_ignores_category(snap: dict, category: str) -> bool:
    if snap.get("ignored"):
        return True
    cats = snap.get("ignored_categories") or []
    return category in cats or "_all" in cats


def _collect_revert_targets(
    detailed: dict,
    history: list[dict],
    category: str,
) -> tuple[list[tuple[str, str, str]], dict]:
    """Return ``[(batch_id, op_id, uid), ...]`` and a stats dict.

    Only includes ``ignore_issue`` ops that genuinely *added* the category
    (before lacked it, after had it) AND whose target seg is still ignored
    for the category today.
    """
    # 1. Build {uid: seg} for segs currently ignored for this category.
    currently_ignored: dict[str, dict] = {}
    for e in detailed.get("entries", []):
        for seg in e.get("segments", []):
            if _is_ignored_for(seg, category):
                uid = seg.get("segment_uid", "")
                if uid:
                    currently_ignored[uid] = seg

    # 2. Find revert candidates from history (skip already-reverted ones).
    already_reverted_op_ids: set[str] = set()
    fully_reverted_batches: set[str] = set()
    for rec in history:
        if rec.get("reverts_batch_id") and rec.get("reverts_op_ids"):
            already_reverted_op_ids.update(rec["reverts_op_ids"])
        elif rec.get("reverts_batch_id"):
            fully_reverted_batches.add(rec["reverts_batch_id"])

    candidates: list[tuple[str, str, str]] = []
    n_ignore_ops_seen = 0
    n_skipped_already_reverted = 0
    n_skipped_no_longer_ignored = 0
    n_skipped_before_already_ignored = 0
    for rec in history:
        if rec.get("reverts_batch_id"):
            continue  # this record IS a revert, not a candidate
        batch_id = rec.get("batch_id", "")
        if batch_id in fully_reverted_batches:
            continue
        for op in rec.get("operations") or []:
            if op.get("op_type") != "ignore_issue":
                continue
            n_ignore_ops_seen += 1
            op_id = op.get("op_id", "")
            if op_id in already_reverted_op_ids:
                n_skipped_already_reverted += 1
                continue
            befores = op.get("targets_before") or []
            afters = op.get("targets_after") or []
            if not befores or not afters:
                continue
            snap_before = befores[0]
            snap_after = afters[0]
            # Only revert if this op *added* the category.
            if _snap_ignores_category(snap_before, category):
                n_skipped_before_already_ignored += 1
                continue
            if not _snap_ignores_category(snap_after, category):
                continue
            uid = snap_after.get("segment_uid", "")
            if uid not in currently_ignored:
                n_skipped_no_longer_ignored += 1
                continue
            candidates.append((batch_id, op_id, uid))

    stats = {
        "currently_ignored_segs": len(currently_ignored),
        "ignore_ops_in_history": n_ignore_ops_seen,
        "skipped_already_reverted": n_skipped_already_reverted,
        "skipped_before_already_ignored": n_skipped_before_already_ignored,
        "skipped_no_longer_ignored": n_skipped_no_longer_ignored,
        "candidates": len(candidates),
    }
    return candidates, stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--slug", required=True)
    ap.add_argument("--category", required=True, help="e.g. cross_verse, qalqala, _all")
    ap.add_argument("--bucket", choices=sorted(_BUCKETS), default="prod")
    ap.add_argument(
        "--apply", action="store_true", help="Mutate. Without this flag the script only reports."
    )
    ap.add_argument(
        "--force-on-drift",
        action="store_true",
        help="When undo_ops fails 409 'times changed' on an "
        "ignore_issue op (later trim/pad moved time bounds "
        "after the ignore landed), bypass the snapshot check "
        "and apply the un-ignore directly. Safe because "
        "_reverse_ignore only touches ignored_categories + "
        "confidence — time bounds are irrelevant to the "
        "mutation. Refuses to force when matched_ref drift "
        "is present (genuine state change).",
    )
    ap.add_argument(
        "--strip-stuck",
        action="store_true",
        help="Second pass: for every seg that is currently "
        "classifier-flagged as ``--category`` AND still "
        "bears the ignore marker after the op-revert pass, "
        "STRIP THE IGNORE ENTIRELY (clears ignored=true and "
        "ignored_categories). Use for legacy `_all` / "
        "`ignored: true` ghosts with no matching ignore_issue "
        "op, and for segs where the original op had real "
        "matched_ref drift. Writes a synthetic "
        "``unignore_orphan`` op into edit_history so the "
        "action is auditable.",
    )
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    _setup_paths_and_env(args.bucket)

    # Imports deferred until sys.path is set.
    from qua_shared.schemas import Actor
    from services.activity.history_query import parse_history_for_reciter
    from services.segments.save import persist_detailed
    from services.segments.undo import _append_revert_record, undo_ops
    from services.state import state as state_service
    from services.storage import cache
    from services.storage.data_loader import load_detailed
    from utils.references import chapter_from_ref

    # Hydrate the state store so the save/undo path sees the reciter's
    # lifecycle state. Per-reciter content lives under a single
    # ``reciters/<slug>/`` prefix regardless of state, so reads no longer
    # depend on hydration — but the undo/save machinery still consults it.
    state_service.hydrate()

    entries = load_detailed(args.slug)
    if not entries:
        log.error("no detailed entries for slug=%s", args.slug)
        return 2
    detailed_doc = {"entries": entries}
    history = parse_history_for_reciter(args.slug) or []
    log.info("loaded detailed entries=%d history records=%d", len(entries), len(history))

    candidates, stats = _collect_revert_targets(
        detailed_doc,
        history,
        args.category,
    )
    log.info("scan: %s", stats)

    if not candidates:
        log.info("nothing to revert; exiting")
        return 0

    by_batch = defaultdict(list)
    for batch_id, op_id, uid in candidates:
        by_batch[batch_id].append((op_id, uid))
    log.info("plan: %d revert ops across %d batches", len(candidates), len(by_batch))

    if not args.apply:
        log.info("DRY RUN — no changes. Re-run with --apply to mutate.")
        # Print first 5 for visibility.
        for batch_id, op_id, uid in candidates[:5]:
            log.info("  would revert: batch=%s op=%s uid=%s", batch_id, op_id, uid)
        if len(candidates) > 5:
            log.info("  ... (%d more)", len(candidates) - 5)
        return 0

    # For force-on-drift fallback we need quick access to the originating op
    # dict by op_id (to read targets_before for restoration).
    op_by_id: dict[str, dict] = {}
    for rec in history:
        if rec.get("reverts_batch_id"):
            continue
        for op in rec.get("operations") or []:
            oid = op.get("op_id")
            if oid:
                op_by_id[oid] = op

    actor = Actor(**_OWNER_ACTOR)
    n_ok = 0
    n_err = 0
    n_forced = 0
    for batch_id, op_pairs in by_batch.items():
        op_ids = {op_id for op_id, _ in op_pairs}
        result = undo_ops(args.slug, batch_id, op_ids, actor=actor)
        if isinstance(result, tuple):
            err, status = result
            if (
                args.force_on_drift
                and status == 409
                and "times changed" in (err.get("error") or "")
            ):
                forced = _force_revert_ignore(
                    args.slug,
                    batch_id,
                    op_pairs,
                    op_by_id,
                    args.category,
                    actor=actor,
                    persist_detailed=persist_detailed,
                    load_detailed=load_detailed,
                    append_revert_record=_append_revert_record,
                    cache=cache,
                    chapter_from_ref=chapter_from_ref,
                )
                if forced:
                    n_forced += 1
                    n_ok += 1
                    log.warning(
                        "batch=%s FORCE-reverted (snapshot drift): %s", batch_id, err.get("error")
                    )
                else:
                    n_err += 1
                    log.error("batch=%s force-revert failed", batch_id)
            else:
                log.error("batch=%s undo failed (status %s): %s", batch_id, status, err)
                n_err += 1
        else:
            n_ok += result.get("operations_reversed", 0)
            log.info("batch=%s OK (%d op reversed)", batch_id, result.get("operations_reversed", 0))

    log.info("op-revert pass done: reverted=%d forced=%d batches_failed=%d", n_ok, n_forced, n_err)

    n_stuck_stripped = 0
    if args.strip_stuck:
        from constants import HISTORY_SCHEMA_VERSION
        from services.storage import data_dir
        from utils.uuid7 import uuid7

        # Re-load: previous reverts may have changed state.
        entries_now = load_detailed(args.slug)
        stuck = _find_stuck_classified_segs(entries_now, args.category)
        log.info(
            "strip-stuck pass: %d seg(s) still classifier-flagged as %s AND ignored",
            len(stuck),
            args.category,
        )
        for seg, entry_ref in stuck:
            n_stuck_stripped += _strip_ignore_record(
                args.slug,
                seg,
                entry_ref,
                args.category,
                actor=actor,
                persist_detailed=persist_detailed,
                append_edit_history=data_dir.append_edit_history,
                append_to_cache=cache.append_history_batch,
                load_detailed=load_detailed,
                cache=cache,
                chapter_from_ref=chapter_from_ref,
                uuid7=uuid7,
                history_schema_version=HISTORY_SCHEMA_VERSION,
            )
        log.info("strip-stuck pass done: stripped=%d", n_stuck_stripped)

    log.info(
        "ALL DONE: total_segs_un_ignored=%d (revert=%d, force=%d, strip=%d)",
        n_ok + n_stuck_stripped,
        n_ok - n_forced,
        n_forced,
        n_stuck_stripped,
    )
    return 0 if n_err == 0 else 1


def _classifier_says_cv(matched_ref: str) -> bool:
    """Mirror inspector classifier's cross_verse rule: s_ayah != e_ayah."""
    if "-" not in matched_ref:
        return False
    a, b = matched_ref.split("-", 1)
    try:
        s = tuple(int(p) for p in a.split(":"))
        e = tuple(int(p) for p in b.split(":"))
    except Exception:
        return False
    return s[0] == e[0] and s[1] != e[1]


def _find_stuck_classified_segs(entries: list[dict], category: str) -> list[tuple[dict, str]]:
    """Return [(seg, entry_ref), ...] for segs that the classifier currently
    flags for ``category`` AND still have any ignore marker.

    Currently only ``cross_verse`` is wired up (uses ``_classifier_says_cv``);
    other categories raise NotImplementedError so we don't silently no-op.
    """
    if category != "cross_verse":
        raise NotImplementedError(f"--strip-stuck not yet implemented for category={category!r}")
    stuck: list[tuple[dict, str]] = []
    for e in entries:
        for seg in e.get("segments", []):
            mr = seg.get("matched_ref", "")
            if not _classifier_says_cv(mr):
                continue
            cats = seg.get("ignored_categories") or []
            if seg.get("ignored") or category in cats or "_all" in cats:
                stuck.append((seg, e.get("ref", "")))
    return stuck


def _strip_ignore_record(
    slug,
    seg,
    entry_ref,
    category,
    *,
    actor,
    persist_detailed,
    append_edit_history,
    append_to_cache,
    load_detailed,
    cache,
    chapter_from_ref,
    uuid7,
    history_schema_version,
) -> int:
    """Strip ALL ignore markers from this seg and append a synthetic
    ``unignore_orphan`` op to edit_history (Option B semantics).

    Returns 1 on success, 0 on failure (no-op safe).
    """
    uid = seg.get("segment_uid", "")
    if not uid:
        return 0

    # Re-load to mutate the on-disk entries (we got a copy from the cached
    # load — go through the storage path so persist_detailed sees fresh state).
    entries = load_detailed(slug)
    target = None
    target_entry_ref = entry_ref
    for e in entries:
        for s in e.get("segments", []):
            if s.get("segment_uid") == uid:
                target = s
                target_entry_ref = e.get("ref", entry_ref)
                break
        if target is not None:
            break
    if target is None:
        log.error("strip: uid=%s vanished between scan and mutate", uid)
        return 0

    # Snapshot before/after for audit.
    snap_before = {
        "segment_uid": uid,
        "matched_ref": target.get("matched_ref"),
        "time_start": target.get("time_start"),
        "time_end": target.get("time_end"),
        "confidence": target.get("confidence"),
        "ignored": target.get("ignored"),
        "ignored_categories": list(target.get("ignored_categories") or []) or None,
    }
    target.pop("ignored", None)
    target.pop("ignored_categories", None)
    snap_after = {
        "segment_uid": uid,
        "matched_ref": target.get("matched_ref"),
        "time_start": target.get("time_start"),
        "time_end": target.get("time_end"),
        "confidence": target.get("confidence"),
    }

    meta = cache.get_seg_meta(slug)
    persist_detailed(slug, meta, entries)

    ch = chapter_from_ref(target_entry_ref) if target_entry_ref else None
    from datetime import datetime

    record = {
        "schema_version": history_schema_version,
        "batch_id": uuid7(),
        "saved_at_utc": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "chapter": ch,
        "operations": [
            {
                "op_id": uuid7(),
                "op_type": "unignore_orphan",
                "fix_kind": "admin",
                "targets_before": [snap_before],
                "targets_after": [snap_after],
            }
        ],
        "actor": actor.model_dump(mode="json"),
    }
    append_edit_history(slug, record)
    append_to_cache(slug, record)
    log.info(
        "strip-stuck: uid=%s mr=%s  before_cats=%s before_ig=%s -> stripped",
        uid,
        snap_before["matched_ref"],
        snap_before["ignored_categories"],
        snap_before["ignored"],
    )
    return 1


def _force_revert_ignore(
    slug: str,
    batch_id: str,
    op_pairs: list[tuple[str, str]],
    op_by_id: dict[str, dict],
    category: str,
    *,
    actor,
    persist_detailed,
    load_detailed,
    append_revert_record,
    cache,
    chapter_from_ref,
) -> bool:
    """Directly apply un-ignore for ops blocked by snapshot time-drift.

    Safe path: only mutate ``ignored_categories`` + ``confidence`` on the live
    seg (matched by uid + matched_ref). Refuses if matched_ref has drifted —
    that's a real state change, not a benign time edit.

    Appends an audit-correct revert record so history stays clean.
    """
    entries = load_detailed(slug)
    if not entries:
        return False

    reverted_op_ids: list[str] = []
    affected_chapters: set[int] = set()

    for op_id, uid in op_pairs:
        op = op_by_id.get(op_id)
        if not op:
            log.error("force-revert: op %s not in history", op_id)
            return False
        befores = op.get("targets_before") or []
        afters = op.get("targets_after") or []
        if not befores or not afters:
            return False
        snap_before = befores[0]
        snap_after = afters[0]
        snap_ref = snap_after.get("matched_ref", "")

        # Locate live seg by uid.
        seg = None
        entry_ref = None
        for e in entries:
            for s in e.get("segments", []):
                if s.get("segment_uid") == uid:
                    seg = s
                    entry_ref = e.get("ref", "")
                    break
            if seg is not None:
                break
        if seg is None:
            log.error("force-revert: uid %s no longer present", uid)
            return False

        # Refuse if matched_ref drifted — that's not a benign time-drift.
        if seg.get("matched_ref", "") != snap_ref:
            log.error(
                "force-revert: uid %s matched_ref drifted (snapshot=%r live=%r) — not forcing",
                uid,
                snap_ref,
                seg.get("matched_ref"),
            )
            return False

        # Apply un-ignore: mirror _restore_ignored_categories + confidence
        # restore from snap_before, exactly as _reverse_ignore would.
        if snap_before.get("ignored_categories"):
            seg["ignored_categories"] = list(snap_before["ignored_categories"])
        else:
            seg.pop("ignored_categories", None)
            seg.pop("ignored", None)
        seg["confidence"] = snap_before.get("confidence", 0)

        reverted_op_ids.append(op_id)
        ch = chapter_from_ref(entry_ref) if entry_ref else None
        if ch is not None:
            affected_chapters.add(ch)

    if not reverted_op_ids:
        return False

    meta = cache.get_seg_meta(slug)
    persist_detailed(slug, meta, entries)

    ch_union = sorted(affected_chapters)
    append_revert_record(
        slug,
        batch_id,
        ch_union[0] if len(ch_union) == 1 else None,
        ch_union if len(ch_union) > 1 else None,
        actor=actor,
        reverts_op_ids=reverted_op_ids,
    )

    cache.pop_seg_caches_affected_by_segment_edit(slug)
    cache.pop_seg_split_group_index(slug)
    return True


if __name__ == "__main__":
    raise SystemExit(main())
