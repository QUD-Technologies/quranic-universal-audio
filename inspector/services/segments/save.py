"""Save flow: write through storage backend, history append, rebuild segments.

No Flask imports -- all functions accept parameters and return plain dicts.

Writes go through ``services.data_dir`` which routes to ``BucketBackend`` or
``FilesystemBackend`` based on ``INSPECTOR_BACKEND``. The bucket backend
writes through the hf-mount FUSE; the daemon's debounced flush handles
durability to remote storage.

Phase 3 made ``@require_edit_lock`` (signed-in + claim + state checks) the
sole authoritative writer gate. The earlier ``INSPECTOR_LOCAL_WRITES`` env
guard is gone — both local and deployed modes route every mutation through
the same OAuth + claim check on the route layer.
"""

from collections import defaultdict
from datetime import UTC, datetime

from adapters.save_payload import build_seg_lookups as _adapter_build_seg_lookups
from adapters.save_payload import make_seg as _adapter_make_seg
from adapters.segments_json import build_segments_doc as _adapter_build_segments_doc
from constants import HISTORY_SCHEMA_VERSION
from domain.command import validate_patch_dict
from qua_shared.riwayat import DEFAULT_SDK_RIWAYAH
from qua_shared.schemas import Actor, FlagFollowUp, SegmentFlag
from services.audio import op_peaks as op_peaks_svc
from services.audio.peaks_history import append_peaks_records
from services.reference.delivery_edition import sdk_riwayah_for
from services.segments.stamping import stamp_segment
from services.storage import cache, data_dir
from services.storage.data_loader import (
    get_single_word_verses,
    get_word_counts,
    load_detailed,
    load_probe_v2,
)
from services.validation.registry import filter_persistent_ignores
from services.validation.snapshot_classifier import classify_snapshot
from utils.references import chapter_from_ref, normalize_ref
from utils.uuid7 import uuid7

# Service result: a success dict (``{"ok": True, ...}``) or an
# ``(error_dict, http_status)`` tuple. The route layer unpacks both.
SaveResult = dict | tuple[dict, int]

# Categories that should not surface in the History panel's per-op
# "classified_issues" deltas. They exist in the validation accordion for
# review/awareness but represent display-only context, not gains/losses
# from an edit.
#   - ``basmala_amin`` flags verse-boundary basmala / amin conventions and
#     would otherwise show up as a noisy +/- pill on every boundary edit.
#   - ``muqattaat`` flags huruf-muqattaʼat verse openings — display-only.
HISTORY_NEUTRAL_CATEGORIES = frozenset({"basmala_amin", "muqattaat"})


def _history_visible_categories(categories: list[str]) -> list[str]:
    return [cat for cat in categories if cat not in HISTORY_NEUTRAL_CATEGORIES]


# Allowed ``command.type`` values.  Both wire-canonical (snake_case
# ``edit_reference`` / ``ignore_issue`` / ``auto_fix_missing_word``) and
# reducer-canonical (camelCase ``editReference`` / ``ignoreIssue`` /
# ``autoFixMissingWord``) shapes are accepted; the dispatchers and history
# round-trips emit the camelCase form, while round-trip and CLI fixtures use
# the snake_case form.
_ALLOWED_COMMAND_TYPES: frozenset[str] = frozenset(
    {
        "trim",
        "split",
        "merge",
        "delete",
        "edit_reference",
        "editReference",
        "ignore_issue",
        "ignoreIssue",
        "auto_fix_missing_word",
        "autoFixMissingWord",
        "set_is_wasl",
        "setIsWasl",
        # Flag a segment with a comment thread (set / edit-or-clear root /
        # append follow-up). Server-authoritative actor + timestamp; not a
        # validation category. Applied by ``_apply_flag_ops``.
        "flag_segment",
        "flagSegment",
        # ``confirm_reference`` is a reducer-edge variant of editReference recorded
        # on ``op_type`` only; the ``command.type`` itself remains ``editReference``.
    }
)


def _validate_command_envelopes(operations: list) -> str | None:
    """Return an error message if any op carries a malformed ``command`` envelope, else None.

    Each operation must carry a ``command`` object whose ``type`` is a known
    string and matches the enclosing ``op.type``.  Ops without a ``type`` (the
    pre-Phase-3 round-trip shape used by patch-only saves) are skipped to
    preserve MUST-1 (additive only).
    """
    for op in operations or []:
        if not isinstance(op, dict):
            continue
        op_type = op.get("type")
        if op_type is None:
            # Patch-style op without a discriminator; nothing to validate.
            continue
        cmd = op.get("command")
        if cmd is None:
            return "operation missing required `command` envelope"
        if not isinstance(cmd, dict):
            return "operation `command` must be a JSON object"
        cmd_type = cmd.get("type")
        if not isinstance(cmd_type, str):
            return "operation `command.type` must be a string"
        if cmd_type not in _ALLOWED_COMMAND_TYPES:
            return f"unknown command.type: {cmd_type!r}"
        if cmd_type != op_type:
            return f"command.type {cmd_type!r} does not match op.type {op_type!r}"
    return None


def _validate_op_patches(operations: list) -> str | None:
    """Return an error message if any op has a malformed ``patch`` field, else None."""
    for op in operations or []:
        if not isinstance(op, dict):
            continue
        patch = op.get("patch")
        if patch is None:
            continue
        err = validate_patch_dict(patch)
        if err:
            return err
    return None


def _ensure_patch_on_ops(operations: list) -> list:
    """Return a copy of *operations* with a ``patch`` field on every op.

    Ops that already carry a ``patch`` are left unchanged.  Ops without one
    receive a minimal empty-patch envelope so the history record always has
    the field (forward-only: new records carry it; inverse-patch path in
    ``services/undo.py`` detects presence via ``"patch" in op``).

    # Forward-only: ops carrying a real patch from applyCommand round-trip
    # correctly through apply_inverse_patch. Ops missing a patch get an empty
    # envelope so undo detection (`if "patch" in op`) routes uniformly. Clients
    # that bypass applyCommand and don't send a patch will see no-op undo;
    # all production save flows must originate from applyCommand to preserve
    # undo correctness.
    """
    out: list = []
    for op in operations or []:
        if not isinstance(op, dict):
            out.append(op)
            continue
        if "patch" not in op:
            new_op = dict(op)
            new_op["patch"] = {
                "before": [],
                "after": [],
                "removedIds": [],
                "insertedIds": [],
                "affectedChapterIds": [],
            }
            out.append(new_op)
        else:
            out.append(op)
    return out


def _attach_classified_issues(
    operations: list,
    probe_failed_uids: set | None = None,
    *,
    riwayah: str,
) -> list:
    """Return a deep-enough copy of ``operations`` with ``classified_issues``
    populated on every snapshot.

    Handles both snapshot shapes the frontend can send:
    - ``op["targets_before"] = [snap, ...]`` and ``op["targets_after"]`` —
      arrays of snapshot dicts (live ops produced by ``snapshotSeg``).
    - ``op["snapshots"] = {"before": snap, "after": snap}`` — singular form
      used by some payload variants (and by the Phase-2 history test).

    Each snapshot dict gains a ``classified_issues: list[str]`` field
    derived by routing through the unified snapshot classifier. Non-dict
    snapshots are left untouched.

    ``probe_failed_uids`` (when provided) lets the snapshot classifier
    surface the *Low Confidence v2* category — same uid set used for the
    live validation pass — so resolved/introduced v2 flags appear in the
    history-row delta with the same ``low conf`` pill as v1.
    """
    out: list = []
    for op in operations or []:
        if not isinstance(op, dict):
            out.append(op)
            continue
        new_op = dict(op)

        for key in ("targets_before", "targets_after"):
            arr = new_op.get(key)
            if not isinstance(arr, list):
                continue
            new_arr: list = []
            for snap in arr:
                if isinstance(snap, dict):
                    enriched = dict(snap)
                    enriched["classified_issues"] = _history_visible_categories(
                        classify_snapshot(
                            enriched,
                            probe_failed_uids=probe_failed_uids,
                            riwayah=riwayah,
                        )
                    )
                    new_arr.append(enriched)
                else:
                    new_arr.append(snap)
            new_op[key] = new_arr

        snapshots = new_op.get("snapshots")
        if isinstance(snapshots, dict):
            new_snapshots = dict(snapshots)
            for which in ("before", "after"):
                snap = new_snapshots.get(which)
                if isinstance(snap, dict):
                    enriched = dict(snap)
                    enriched["classified_issues"] = _history_visible_categories(
                        classify_snapshot(
                            enriched,
                            probe_failed_uids=probe_failed_uids,
                            riwayah=riwayah,
                        )
                    )
                    new_snapshots[which] = enriched
            new_op["snapshots"] = new_snapshots

        out.append(new_op)
    return out


def persist_detailed(reciter: str, meta: dict, entries: list[dict]) -> None:
    """Write detailed.json atomically; rebuild segments.json.

    Shared helper consumed by both undo.py and (internally) save_seg_data.
    Does NOT append history — callers are responsible for that.

    v2: writes go through ``data_dir`` (storage backend). No ``.bak`` files
    (audit log + edit_history.jsonl are the recovery surface). No file
    hash returned — the file-hash chain is removed in v2.
    """
    # Strip transient ``_resolved_by_edit`` (set, injected by validate route)
    # — concurrent validate may have left it on cached segs mid-flight.
    for entry in entries:
        for seg in entry.get("segments", []):
            seg.pop("_resolved_by_edit", None)
    data_dir.write_detailed_doc(reciter, {"_meta": meta, "entries": entries})
    rebuild_segments_json(reciter, entries)


def normalize_ref_with_wc(ref: str, riwayah: str = DEFAULT_SDK_RIWAYAH) -> str:
    """Normalize a short ref to canonical form (passes cached word counts).

    The word counts are the EDITION's: a short ref like ``2:285`` expands to
    that edition's last word of that verse, and Warsh's al-Baqarah has one
    fewer verse than Hafs's with different word counts throughout.
    """
    return normalize_ref(ref, get_word_counts(riwayah))


def rebuild_segments_json(reciter: str, entries: list[dict]) -> None:
    """Regenerate segments.json from detailed entries (verse-aggregated format).

    Reads the existing ``_meta`` block from the on-bucket ``segments.json``
    so the freshly-built doc preserves the meta block intact, then writes the
    rebuilt doc via the storage backend.
    """
    existing = data_dir.read_segments_doc(reciter)
    existing_meta = (existing or {}).get("_meta", {})
    seg_doc = _adapter_build_segments_doc(entries, existing_meta)
    data_dir.write_segments_doc(reciter, seg_doc)


# ---------------------------------------------------------------------------
# save_seg_data — phase helpers
# Each helper owns one sequential phase of the orchestrator below.
# `_make_seg` is promoted to module-level with explicit lookup params.
# ---------------------------------------------------------------------------


def _build_seg_lookups(matching: list[dict]) -> tuple[dict, dict]:
    """Build ``(by_time, by_uid)`` lookups of existing segments for field preservation.

    Thin wrapper over ``adapters.save_payload.build_seg_lookups`` — kept under
    the ``_build_seg_lookups`` name because internal helpers reference it and
    pytest patches it in some cases.
    """
    return _adapter_build_seg_lookups(matching)


def _make_seg(
    s: dict,
    existing_by_time: dict,
    existing_by_uid: dict,
    word_counts: dict | None = None,
) -> dict:
    """Build a canonical segment dict, preserving fields from an existing match if any.

    Delegates to ``adapters.save_payload.make_seg``.  ``word_counts`` is
    resolved lazily via ``get_word_counts()`` when the caller does not supply
    one — preserving the historical behaviour where each call site relied on
    the ``services.cache``-backed lazy load.  Hot loops that build a single
    save batch should resolve once and pass the resolved dict explicitly.
    """
    if word_counts is None:
        word_counts = get_word_counts()
    return _adapter_make_seg(s, existing_by_time, existing_by_uid, word_counts)


def _apply_full_replace(
    matching: list[dict],
    updates: dict,
    existing_by_time: dict,
    existing_by_uid: dict,
    riwayah: str = DEFAULT_SDK_RIWAYAH,
):
    """Mutate ``matching`` in place for a full_replace save.

    Returns ``None`` on success or an ``(error_dict, http_status)`` tuple on
    input validation failure (propagated by the caller as the route response).
    """
    word_counts = get_word_counts(riwayah)
    single_word_verses = get_single_word_verses(riwayah)
    if len(matching) == 1:
        new_segs = [
            _make_seg(s, existing_by_time, existing_by_uid, word_counts)
            for s in updates["segments"]
        ]
        for seg in new_segs:
            stamp_segment(seg, single_word_verses, riwayah)
        matching[0]["segments"] = new_segs
        return None

    entry_by_audio: dict[str, list[dict]] = defaultdict(list)
    for e in matching:
        audio = e.get("audio", "")
        if audio:
            entry_by_audio[audio].append(e)
        e["segments"] = []

    for s in updates["segments"]:
        seg_audio = s.get("audio_url", "")
        if not seg_audio:
            return {
                "error": (
                    "Rejected structural save for by_ayah: segment payload is "
                    "missing audio_url. Reload Inspector and try again."
                )
            }, 400

        candidates = entry_by_audio.get(seg_audio, [])
        if len(candidates) != 1:
            if len(candidates) == 0:
                return {
                    "error": (
                        "Rejected structural save for by_ayah: segment audio_url "
                        "does not belong to this chapter."
                    )
                }, 400
            return {
                "error": (
                    "Rejected structural save for by_ayah: ambiguous audio_url "
                    "matched multiple chapter entries."
                )
            }, 400

        new_seg = _make_seg(s, existing_by_time, existing_by_uid, word_counts)
        stamp_segment(new_seg, single_word_verses, riwayah)
        candidates[0]["segments"].append(new_seg)
    return None


def _apply_patch(matching: list[dict], updates: dict, riwayah: str = DEFAULT_SDK_RIWAYAH) -> None:
    """Mutate ``matching`` in place for a patch save (field-level updates by index)."""
    flat_segments = []
    for e in matching:
        for seg in e.get("segments", []):
            flat_segments.append(seg)

    single_word_verses = get_single_word_verses(riwayah)
    for upd in updates["segments"]:
        idx = upd.get("index")
        if idx is not None and 0 <= idx < len(flat_segments):
            ref = normalize_ref_with_wc(upd.get("matched_ref", ""), riwayah)
            if ref != flat_segments[idx].get("matched_ref"):
                flat_segments[idx].pop("word_timings", None)
            flat_segments[idx]["matched_ref"] = ref
            if "confidence" in upd:
                flat_segments[idx]["confidence"] = upd["confidence"]
            if "ignored_categories" in upd:
                ic = filter_persistent_ignores(upd.get("ignored_categories") or [])
                if ic:
                    flat_segments[idx]["ignored_categories"] = ic
                else:
                    flat_segments[idx].pop("ignored_categories", None)
                    flat_segments[idx].pop("ignored", None)
            # Re-stamp persisted classifier fields since matched_ref/text changed.
            stamp_segment(flat_segments[idx], single_word_verses, riwayah)


def _utc_now_iso() -> str:
    """Millisecond ISO-8601 UTC stamp with a ``Z`` suffix (batch-clock format)."""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _mutate_seg_flag(seg: dict, intent: str, comment: str, actor: Actor, now: str):
    """Apply one flag intent to ``seg`` in place. Returns ``None`` or ``(err, status)``.

    Intents:
      - ``set`` — create a flag (comment required).
      - ``edit`` — replace the root comment (flagger-only); an empty comment
        removes the whole flag (the unflag mechanism).
      - ``followup`` — append a reply (any editor; comment required).

    The flag is always rebuilt through ``SegmentFlag`` so ``actor`` and the
    timestamps stay server-authoritative — the client never supplies identity.
    """
    existing = seg.get("flag")
    if intent == "set":
        if not comment:
            return {"error": "A comment is required to flag a segment."}, 400
        seg["flag"] = SegmentFlag(comment=comment, actor=actor, flagged_at_utc=now).model_dump(
            mode="json"
        )
        return None

    if intent == "edit":
        if not existing:
            return {"error": "No flag to edit on this segment."}, 404
        flag = SegmentFlag.model_validate(existing)
        if flag.actor.hf_user_id != actor.hf_user_id:
            return {"error": "Only the flagger can edit this comment."}, 403
        if not comment:
            seg.pop("flag", None)  # clearing the comment removes the flag
            return None
        seg["flag"] = flag.model_copy(update={"comment": comment}).model_dump(mode="json")
        return None

    if intent == "followup":
        if not existing:
            return {"error": "No flag to reply to on this segment."}, 404
        if not comment:
            return {"error": "A comment is required for a follow-up."}, 400
        flag = SegmentFlag.model_validate(existing)
        reply = FlagFollowUp(comment=comment, actor=actor, at_utc=now)
        seg["flag"] = flag.model_copy(update={"follow_ups": [*flag.follow_ups, reply]}).model_dump(
            mode="json"
        )
        return None

    return {"error": f"unknown flag intent: {intent!r}"}, 400


def _flag_ref(seg: dict) -> str:
    """A human ``surah:ayah`` locator from a seg's ``matched_ref`` for the
    notification body. ``"2:255:1-2:255:5"`` → ``"2:255"``; a special transition
    token (``Basmala`` …) passes through; anything unparseable → ``"segment"``."""
    mr = str(seg.get("matched_ref") or "")
    parts = mr.split("-")[0].split(":")
    if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
        return f"{parts[0]}:{parts[1]}"
    return mr or "segment"


def _apply_flag_ops(matching: list[dict], operations: list, *, actor: Actor):
    """Apply every ``flag_segment`` op in ``operations`` to ``matching`` in place.

    Resolves the target by ``segment_uid`` against the current (post-apply)
    segments so it composes with any patch/full_replace in the same batch.
    Returns ``(err_or_none, replies, owner_activity)``: ``err`` is ``None`` on
    success or an ``(error_dict, http_status)`` tuple; ``replies`` describes each
    follow-up on a flag owned by someone other than the actor (notify the
    original flagger); ``owner_activity`` describes each new flag + reply for the
    owner-facing review alerts (notify the review-alert recipients). Edits /
    unflags produce neither.
    """
    flag_ops = [
        op for op in operations if isinstance(op, dict) and op.get("op_type") == "flag_segment"
    ]
    if not flag_ops:
        return None, [], []

    by_uid: dict[str, dict] = {}
    for e in matching:
        for seg in e.get("segments", []):
            uid = seg.get("segment_uid")
            if uid:
                by_uid[uid] = seg

    now = _utc_now_iso()
    replies: list[dict] = []
    owner_activity: list[dict] = []
    for op in flag_ops:
        cmd = op.get("command") or {}
        uid = cmd.get("segmentUid")
        seg = by_uid.get(uid) if uid is not None else None
        if seg is None:
            return (
                ({"error": f"flag target segment not found: {uid!r}"}, 404),
                replies,
                owner_activity,
            )
        intent = cmd.get("intent") or "set"
        comment = (cmd.get("comment") or "").strip()
        # Capture the original flagger BEFORE the reply is appended.
        prior_flagger = ((seg.get("flag") or {}).get("actor") or {}).get("hf_user_id")
        err = _mutate_seg_flag(seg, intent, comment, actor, now)
        if err is not None:
            return err, replies, owner_activity
        if intent == "followup" and prior_flagger and prior_flagger != actor.hf_user_id:
            replies.append(
                {"flagger_id": prior_flagger, "segment_uid": uid, "comment": comment, "at_utc": now}
            )
        if intent in ("set", "followup") and comment:
            owner_activity.append(
                {
                    "segment_uid": uid,
                    "kind": "created" if intent == "set" else "replied",
                    "ref": _flag_ref(seg),
                    "comment": comment,
                    "at_utc": now,
                }
            )
    return None, replies, owner_activity


def _persist_and_record(
    reciter: str,
    chapter: int,
    entries: list[dict],
    meta: dict,
    updates: dict,
    *,
    actor: Actor,
    riwayah: str,
) -> SaveResult:
    """Persist mutated entries to disk, append edit_history, invalidate caches."""
    # Validate patch envelopes before writing anything.
    raw_ops = updates.get("operations", [])
    patch_err = _validate_op_patches(raw_ops)
    if patch_err:
        return {"error": patch_err}, 400

    # Write detailed.json + rebuild segments.json via storage backend.
    persist_detailed(reciter, meta, entries)

    # Each operation's snapshots gain a ``classified_issues`` field so the
    # frontend history-delta path reads it directly off the saved record
    # instead of running a second classifier pass on snapshot dicts.
    # Ops also receive a ``patch`` envelope when absent.
    probe_failed_uids, _ = load_probe_v2(reciter)
    operations = _attach_classified_issues(
        _ensure_patch_on_ops(raw_ops),
        probe_failed_uids=probe_failed_uids,
        riwayah=riwayah,
    )
    # ``actor`` block carries the per-edit attribution surfaced in the
    # History panel and feeding the contributor-recognition page.
    batch = {
        "schema_version": HISTORY_SCHEMA_VERSION,
        "batch_id": uuid7(),
        "chapter": chapter,
        "saved_at_utc": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "operations": operations,
        "actor": actor.model_dump(mode="json"),
    }
    data_dir.append_edit_history(reciter, batch)

    # Generate per-op waveform peaks by slicing the baked 10 bps chapter peaks
    # for each op's covering range, so the History panel renders cross-session
    # (incl. anonymous) without re-computing. Cheap (int8 slice + b64, no
    # ffmpeg) and complete for every saved op whose chapter peaks are baked.
    # Best-effort: a peaks failure must never fail the save (lazy compute-on-
    # play fills any gaps). Dedup-by-op_id lives in append_peaks_records.
    try:
        by_chapter = op_peaks_svc.audio_url_by_chapter(reciter)
        recs = op_peaks_svc.build_op_records(
            reciter,
            operations,
            by_chapter,
            default_chapter=chapter,
        )
        if recs:
            append_peaks_records(reciter, recs, batch_id=batch["batch_id"])
    except Exception:  # noqa: BLE001 — peaks are best-effort, never block a save
        import logging

        logging.getLogger(__name__).exception(
            "[%s] history-peaks generation failed during save (ch %s)",
            reciter,
            chapter,
        )

    # Surgical cache invalidation. Edit-history-derived caches survive
    # the autosave warm path by being appended/extended in place; auto-split
    # only evicts when the uid set changed; pipeline_meta is immutable.
    cache.pop_seg_caches_affected_by_segment_edit(reciter)
    cache.append_history_batch(reciter, batch)
    _refresh_split_group_index_on_save(reciter, batch)
    if cache.batch_changes_segment_set(batch):
        cache.pop_seg_auto_split(reciter)

    return {"ok": True}


def _refresh_split_group_index_on_save(reciter: str, batch: dict) -> None:
    """Invalidate the cached split-group index when a save contains split ops.

    Pops the cache so the next reader rebuilds from the (already-appended)
    batch list — pure in-memory walk, no I/O. Pre-computing the new closure
    inline would require re-running the fixpoint over an existing index plus
    the new ops, same cost as a full rebuild for typical batch sizes with
    more code surface. No-op if the cache is empty or the batch has no
    split ops.
    """
    cached = cache.get_seg_split_group_index(reciter)
    if cached is None:
        return
    has_split = any(
        op.get("op_type") == "split_segment" or op.get("kind") == "split_segment"
        for op in batch.get("operations") or []
    )
    if not has_split:
        return
    cache.pop_seg_split_group_index(reciter)


def save_seg_data(reciter: str, chapter: int, updates: dict, *, actor: Actor) -> SaveResult:
    """Save edited segments.  Returns ``{"ok": True}`` or ``{"error": ...}``
    with an HTTP status code as a second element in a tuple.

    ``actor`` (kw-only) carries the per-edit attribution stamped on every
    new edit_history.jsonl batch. The route layer builds it from the
    authenticated user (see ``inspector/routes/segments_edit.py``).
    """
    # Validate command envelopes on every op before any work is done.  Each
    # op declaring a discriminated ``type`` must carry a matching ``command``
    # object whose ``type`` is in the allowed set.  Rejection is additive
    # (MUST-1): historical patch-style ops without a ``type`` discriminator
    # pass through untouched.
    cmd_err = _validate_command_envelopes(updates.get("operations") or [])
    if cmd_err:
        return {"error": cmd_err}, 400

    entries = load_detailed(reciter)
    if not entries:
        return {"error": "Reciter not found"}, 404

    matching = [e for e in entries if chapter_from_ref(e["ref"]) == chapter]
    if not matching:
        return {"error": "Chapter not found"}, 404

    # Build lookups of existing segments by time and by uid for field preservation
    existing_by_time, existing_by_uid = _build_seg_lookups(matching)

    meta = cache.get_seg_meta(reciter)
    # Refs, word counts and the persisted classifier fields are all
    # edition-specific. Raises rather than defaulting to Hafs — a save is the
    # one place a wrong edition becomes permanent on disk.
    riwayah = sdk_riwayah_for(reciter)

    # ``entries`` IS the process cache — ``load_detailed`` hands back
    # ``cache.get_seg_cache(reciter)`` by reference — so the apply steps below
    # mutate it before anything is written. A save that bails after them (a
    # rejected patch envelope, a ref that is not in the delivery's edition)
    # would otherwise leave those never-persisted edits visible to every later
    # request, so the cache is dropped on any exit that is not a persist.
    persisted = False
    try:
        if updates.get("full_replace"):
            err = _apply_full_replace(matching, updates, existing_by_time, existing_by_uid, riwayah)
            if err is not None:
                return err
        else:
            _apply_patch(matching, updates, riwayah)

        # Flag ops carry their payload in the operation envelope, not in
        # ``segments`` — applied here with a server-authoritative actor + clock.
        flag_err, flag_replies, flag_owner_activity = _apply_flag_ops(
            matching, updates.get("operations") or [], actor=actor
        )
        if flag_err is not None:
            return flag_err

        # ``ignored_categories`` is mutated only when the payload explicitly
        # carries it (Ignore action, or explicit ``[]`` clear -- MUST-7).
        # Edits dispatched from validation accordion cards no longer write to
        # ``ignored_categories``: that contract is reserved for explicit Ignore.
        # Card dismissal for soft-rule categories is purely a frontend
        # session-state concern.
        result = _persist_and_record(
            reciter,
            chapter,
            entries,
            meta,
            updates,
            actor=actor,
            riwayah=riwayah,
        )
        persisted = not isinstance(result, tuple)
    finally:
        if not persisted:
            cache.invalidate_seg_caches(reciter)

    # Notify after the save persisted — best-effort (own durable txn), never
    # affects the save. Two audiences: the original flagger on a reply to their
    # flag, and the review-alert recipients on any new flag / reply.
    if not isinstance(result, tuple):
        from services.notifications import emit as _notify

        for r in flag_replies:
            _notify.notify_flag_reply(
                flagger_id=r["flagger_id"],
                replier=actor,
                slug=reciter,
                segment_uid=r["segment_uid"],
                comment=r["comment"],
                at_utc=r["at_utc"],
            )
        for a in flag_owner_activity:
            _notify.notify_owners_flag_activity(
                actor=actor,
                slug=reciter,
                segment_uid=a["segment_uid"],
                kind=a["kind"],
                body=f"{a['ref']} — {a['comment']}",
                at_utc=a["at_utc"],
            )
    return result
