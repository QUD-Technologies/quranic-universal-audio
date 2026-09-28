"""Review recovery, merge guards, reversible audit and immutable snapshot staging."""

import copy
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts/backfills/backfill_join_verdicts.py"
spec = importlib.util.spec_from_file_location("backfill_join_verdicts", SCRIPT)
backfill = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backfill)


def seg(uid="root", start=0, end=900, ref="1:1:1-1:1:4", **extra):
    return dict(
        segment_uid=uid, time_start=start, time_end=end, matched_ref=ref, confidence=0.9, **extra
    )


def doc(*segs):
    return {"entries": [{"ref": "1", "segments": list(segs) or [seg()]}]}


def history(kind="ignore_issue", before=None, after=None, category="missed_waqf"):
    return [
        {
            "batch_id": "b",
            "chapter": 1,
            "operations": [
                {
                    "op_id": "o",
                    "op_type": kind,
                    "op_context_category": category,
                    "targets_before": before or [seg()],
                    "targets_after": after or [seg()],
                }
            ],
        }
    ]


SIDE = {
    "by_uid": {
        "root": {"cursors": [300, 600], "refs": ["1:1:1-1:1:1", "1:1:2-1:1:2", "1:1:3-1:1:4"]}
    }
}


def test_ignore_recovers_every_cursor_and_is_idempotent():
    source = doc()
    after, rows, ops = backfill.plan_backfill(source, history(), SIDE)
    assert [j["verdict"] for j in after["entries"][0]["segments"][0]["join_verdicts"]] == [
        "wasl",
        "wasl",
    ]
    assert backfill.counts_for(rows)["mw_wasl_ignore"] == 2
    assert "join_verdicts" not in source["entries"][0]["segments"][0]
    assert "is_wasl" not in after["entries"][0]["segments"][0]
    again, rows, ops2 = backfill.plan_backfill(after, history(), SIDE)
    assert again == after and not ops2
    assert {r["status"] for r in rows} == {"unchanged"}
    assert ops[0]["patch"]["before"][0].get("join_verdicts") is None


def test_mixed_split_and_changed_uid_place_on_current_owners():
    left, right = seg(end=600, ref="1:1:1-1:1:2"), seg("child", 600, 900, "1:1:3-1:1:4")
    h = history("split_segment", after=[left, right])
    left = {**left, "segment_uid": "current"}
    after, rows, _ = backfill.plan_backfill(doc(left, right), h, SIDE)
    assert after["entries"][0]["segments"][0]["join_verdicts"] == [
        {"after_ref": "1:1:1", "at_ms": 300, "verdict": "wasl"},
        {"after_ref": "1:1:2", "at_ms": 600, "verdict": "waqf"},
    ]
    counts = backfill.counts_for(rows)
    assert counts["mw_waqf"] == counts["mw_wasl_mixed"] == 1


@pytest.mark.parametrize(
    "revert", [{"reverts_batch_id": "b"}, {"reverts_batch_id": "b", "reverts_op_ids": ["o"]}]
)
def test_reverted_ops_are_reported_unset(revert):
    after, rows, ops = backfill.plan_backfill(
        doc(), [*history(), {"batch_id": "undo", **revert}], SIDE
    )
    assert after == doc() and not ops
    assert len(rows) == 2 and {r["reason"] for r in rows} == {"reverted_operation"}


def test_changed_geometry_conflicts_and_missing_coordinates_are_reported():
    _, rows, _ = backfill.plan_backfill(doc(seg(ref="1:2:1-1:2:4")), history(), SIDE)
    assert {r["reason"] for r in rows} == {"geometry_or_ref_changed"}
    conflict = seg(join_verdicts=[{"at_ms": 300, "after_ref": "1:1:1", "verdict": "waqf"}])
    _, rows, _ = backfill.plan_backfill(doc(conflict), history(), SIDE)
    assert rows[0]["reason"] == "conflicting_explicit_verdict"
    _, rows, _ = backfill.plan_backfill(doc(), history(), {})
    assert rows[0]["reason"] == "missing_sidecar_coordinates"


@pytest.mark.parametrize("category", ["cross_verse", None])
def test_verse_end_split_records_waqf_only_at_cut(category):
    a, b = seg(end=300), seg("b", 300, 900, "1:2:1-1:2:4")
    _, rows, ops = backfill.plan_backfill(
        doc(a, b), history("split_segment", after=[a, b], category=category)
    )
    assert backfill.counts_for(rows)["cv_waqf"] == 1
    assert ops[0]["targets_after"][0]["join_verdicts"] == [
        {"at_ms": 300, "after_ref": "1:1:4", "verdict": "waqf"}
    ]


def test_merge_chain_keeps_first_uid_min_confidence_and_audits_each_merge():
    a = seg(end=300, is_wasl=True, ignored_categories=["a"], confidence_dummy=None)
    a.pop("confidence_dummy")
    b = seg("b", 310, 600, "1:2:1-1:2:4", is_wasl=True, ignored_categories=["b"])
    b["confidence"] = 0.6
    c = seg("c", 600, 900, "1:3:1-1:3:2", word_timings=[])
    after, rows, ops = backfill.plan_backfill(doc(a, b, c), [])
    (merged,) = after["entries"][0]["segments"]
    assert merged["segment_uid"] == "root"
    assert merged["confidence"] == 0.6 and merged["ignored_categories"] == ["a", "b"]
    assert merged["matched_ref"] == "1:1:1-1:3:2"
    assert "is_wasl" not in merged and "word_timings" not in merged
    assert len(merged["join_verdicts"]) == 2
    assert len(ops) == 2 and all(o["op_type"] == "merge_segments" for o in ops)
    assert ops[0]["patch"]["removedIds"] == ["b"] and ops[1]["patch"]["removedIds"] == ["c"]
    assert backfill.counts_for(rows)["merge_chains"] == 1
    from qua_shared.segment_edit_ops import batch_affects_timestamps

    batches = backfill.audit_batches(ops, "2026-09-28T00:00:00Z")
    assert all(batch_affects_timestamps(b) for b in batches)
    assert batches[0]["actor"]["login_at_time"] == "join-verdict-backfill"
    again, _, ops2 = backfill.plan_backfill(after, batches)
    assert again == after and not ops2


@pytest.mark.parametrize(
    "reason",
    [
        "no_next_segment",
        "different_chapter",
        "different_entry",
        "different_audio",
        "not_immediate_in_time",
        "special_or_unmatched",
        "wrap_word_ranges",
        "flagged",
        "time_overlap",
        "noncontiguous_refs",
        "conflicting_explicit_verdict",
        "missing_segment_uid",
    ],
)
def test_every_merge_skip_reason(reason):
    a, b = seg(end=300, is_wasl=True), seg("b", 300, 900, "1:2:1-1:2:4")
    e = {"ref": "1"}
    re, immediate = e, True
    if reason == "no_next_segment":
        b = None
    elif reason == "different_chapter":
        re = {"ref": "2"}
    elif reason == "different_entry":
        re = {"ref": "1:2"}
    elif reason == "different_audio":
        b["audio_url"] = "another.mp3"
    elif reason == "not_immediate_in_time":
        immediate = False
    elif reason == "special_or_unmatched":
        b["matched_ref"] = "Basmala"
    elif reason == "wrap_word_ranges":
        b["wrap_word_ranges"] = [["1:2:1"]]
    elif reason == "flagged":
        b["flag"] = {"comment": "check"}
    elif reason == "time_overlap":
        b["time_start"] = 299
    elif reason == "noncontiguous_refs":
        b["matched_ref"] = "1:2:2-1:2:4"
    elif reason == "conflicting_explicit_verdict":
        a["join_verdicts"] = [backfill.answer(a)]
    elif reason == "missing_segment_uid":
        b.pop("segment_uid")
    assert backfill.merge_skip(a, b, e, re, "hafs", immediate) == reason


def test_failed_next_merge_keeps_last_piece_flag():
    a, b = seg(end=300, is_wasl=True), seg("b", 300, 600, "1:2:1-1:2:4", is_wasl=True)
    c = seg("c", 600, 900, "1:3:2-1:3:2")
    after, rows, _ = backfill.plan_backfill(doc(a, b, c), [])
    assert len(after["entries"][0]["segments"]) == 2
    assert after["entries"][0]["segments"][0]["is_wasl"] is True
    assert rows[-1]["reason"] == "noncontiguous_refs"


def test_wasl_recovery_finishes_after_merges_in_one_run():
    left = seg(end=300, is_wasl=True)
    right = seg("b", 300, 900, "1:2:1-1:2:4")
    parent = seg(ref="1:1:1-1:2:4")
    sidecar = {"by_uid": {"root": {"cursors": [250], "refs": ["1:1:1-1:2:1", "1:2:2-1:2:4"]}}}
    h = history(before=[parent], after=[parent])
    after, rows, ops = backfill.plan_backfill(doc(left, right), h, sidecar)
    assert backfill.counts_for(rows)["mw_wasl_ignore"] == 1
    assert ops[-1]["op_type"] == "join_verdict_backfill"
    again, _, repeat_ops = backfill.plan_backfill(after, h, sidecar)
    assert again == after and repeat_ops == []


def test_child_review_follows_effective_split_ancestry():
    first = seg(end=300, ref="1:1:1-1:1:1")
    child = seg("child", 300, 900, "1:1:2-1:1:4")
    h = history("split_segment", after=[first, child])
    second = history(before=[child], after=[child])[0]
    second["batch_id"] = "b2"
    second["operations"][0]["op_id"] = "o2"
    after, rows, _ = backfill.plan_backfill(doc(first, child), [*h, second], SIDE)
    assert after["entries"][0]["segments"][1]["join_verdicts"] == [
        {"at_ms": 600, "after_ref": "1:1:2", "verdict": "wasl"}
    ]
    assert not any(r.get("reason") == "missing_sidecar_coordinates" for r in rows)


def export(tmp_path):
    source = tmp_path / "snapshot"
    rec = source / "voice"
    rec.mkdir(parents=True)
    (rec / "detailed.json").write_text(json.dumps(doc()), encoding="utf-8")
    (rec / "edit_history.jsonl").write_text(json.dumps(history()[0]), encoding="utf-8")
    (rec / "missed_waqf_v1.json").write_text(json.dumps(SIDE), encoding="utf-8")
    return source


def test_cli_dry_run_staging_readback_and_idempotence(tmp_path, capsys):
    source = export(tmp_path)
    original = {p: p.read_bytes() for p in source.glob("*/*")}
    dry, staged = tmp_path / "dry", tmp_path / "staged"
    assert backfill.main(["--input", str(source), "--report-dir", str(dry)]) == 0
    report = json.loads((dry / "report.json").read_text())
    assert report["totals"]["mw_wasl_ignore"] == 2
    assert not report["timestamps_would_go_stale"]
    assert (dry / "summary.txt").exists()
    assert backfill.main(["--input", str(source), "--apply", "--output", str(staged)]) == 0
    assert original == {p: p.read_bytes() for p in original}
    history_raw = (staged / "voice/edit_history.jsonl").read_bytes()
    assert history_raw.startswith(original[source / "voice/edit_history.jsonl"])
    audit = json.loads(history_raw.splitlines()[-1])
    assert audit["batch_type"] == "join_verdict_backfill"
    report2 = tmp_path / "again.json"
    assert backfill.main(["--input", str(staged), "--report", str(report2)]) == 0
    assert json.loads(report2.read_text())["totals"]["segments_changed"] == 0
    with pytest.raises(SystemExit):
        backfill.main(["--input", str(source), "--apply", "--output", str(staged)])


def test_snapshot_mismatch_refused_before_any_output(tmp_path):
    source = export(tmp_path)
    live = tmp_path / "live"
    (live / "voice").mkdir(parents=True)
    (live / "voice/detailed.json").write_text(json.dumps(doc(seg(end=1000))), encoding="utf-8")
    output = tmp_path / "staged"
    with pytest.raises(ValueError, match="snapshot mismatch: voice"):
        backfill.main(
            ["--input", str(source), "--apply", "--output", str(output), "--live-input", str(live)]
        )
    assert not output.exists()
    original = {source / "voice/detailed.json": b"changed"}
    with pytest.raises(ValueError, match="snapshot changed"):
        backfill.verify_snapshot(source, original)


def test_validate_all_inputs_before_output_and_preserve_snapshot_fields(tmp_path):
    source = export(tmp_path)
    p = source / "voice/detailed.json"
    d = doc(seg(matched_text="example", chapter=1))
    after, _, _ = backfill.plan_backfill(d, [], {})
    assert after == d
    d["entries"][0]["segments"][0]["unexpected"] = True
    p.write_text(json.dumps(d), encoding="utf-8")
    with pytest.raises(ValueError):
        backfill.main(["--input", str(source), "--apply", "--output", str(tmp_path / "staged")])
    assert not (tmp_path / "staged").exists()
