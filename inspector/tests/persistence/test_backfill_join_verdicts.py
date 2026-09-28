"""Conservative recovery, immutable inputs, dry-run default and idempotence."""

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts/backfills/backfill_join_verdicts.py"
spec = importlib.util.spec_from_file_location("backfill_join_verdicts", SCRIPT)
backfill = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backfill)


def segment(**extra):
    return {
        "segment_uid": "root",
        "time_start": 100,
        "time_end": 500,
        "matched_ref": "1:3:1-1:3:2",
        "confidence": 0.9,
        **extra,
    }


def document(**extra):
    return {"entries": [{"ref": "1", "segments": [segment(**extra)]}]}


def batch(kind="set_is_wasl", snap=None, **op_extra):
    return {
        "batch_id": "b1",
        "saved_at_utc": "2026-09-01T00:00:00Z",
        "chapter": 1,
        "operations": [
            {
                "op_id": "o1",
                "op_type": kind,
                "targets_before": [segment()],
                "targets_after": [snap or segment()],
                **op_extra,
            }
        ],
    }


@pytest.mark.parametrize("value", [False, True])
def test_explicit_legacy_answer_and_idempotence(value):
    doc = document()
    history = [batch(snap=segment(is_wasl=value))]
    after, rows = backfill.plan_backfill(doc, history)
    expected = "wasl" if value else "waqf"
    assert after["entries"][0]["segments"][0]["join_verdicts"][0]["verdict"] == expected
    assert rows[0]["status"] == "recoverable"
    assert "join_verdicts" not in doc["entries"][0]["segments"][0]
    again, rows = backfill.plan_backfill(after, history)
    assert again == after
    assert rows[0]["status"] == "unchanged"


def test_omitted_false_on_explicit_set_is_a_waqf_answer():
    _, rows = backfill.plan_backfill(document(), [batch()])
    assert rows[0]["answer"]["verdict"] == "waqf"


@pytest.mark.parametrize(
    "revert", [{"reverts_batch_id": "b1"}, {"reverts_batch_id": "b1", "reverts_op_ids": ["o1"]}]
)
def test_reverted_evidence_is_not_recovered(revert):
    after, rows = backfill.plan_backfill(document(), [batch(), {"batch_id": "undo", **revert}])
    assert after == document()
    assert rows == []


def test_stale_geometry_and_pending_recheck():
    _, rows = backfill.plan_backfill(document(time_end=600), [batch()])
    assert rows[0]["status"] == "stale_geometry"
    sidecar = {"_meta": {"created_at": "2026-09-02T00:00:00Z"}, "by_uid": {"root": {}}}
    _, rows = backfill.plan_backfill(document(), [batch()], sidecar)
    assert rows[0]["status"] == "recheck_pending"


def test_geometry_edit_invalidates_earlier_edge_evidence():
    changed = segment(time_end=600)
    later = batch("trim_segment", changed)
    later["batch_id"] = "b2"
    later["operations"][0]["op_id"] = "o2"
    after, rows = backfill.plan_backfill(document(time_end=600), [batch(), later])
    assert "join_verdicts" not in after["entries"][0]["segments"][0]
    assert rows == []


def test_ignore_and_unlabelled_split_are_ambiguous():
    ignored = batch("ignore_issue", op_context_category="missed_waqf")
    _, rows = backfill.plan_backfill(document(), [ignored])
    assert rows[0]["status"] == "ambiguous"
    split = batch("split_segment")
    split["operations"][0]["targets_after"].append(
        segment(segment_uid="right", time_start=500, time_end=900)
    )
    _, rows = backfill.plan_backfill(document(), [split])
    assert rows[0]["status"] == "ambiguous"
    split["operations"][0].update(op_context_category="missed_waqf", command={"wasls": [False]})
    _, rows = backfill.plan_backfill(document(), [split])
    assert rows[0]["status"] == "recoverable"


def test_existing_conflicting_verdict_is_never_overwritten():
    answer = {"at_ms": 500, "after_ref": "1:3:2", "verdict": "wasl"}
    doc = document(join_verdicts=[answer])
    after, rows = backfill.plan_backfill(doc, [batch()])
    assert after == doc
    assert rows[0]["status"] == "conflict"


def test_cli_dry_run_apply_audit_and_rerun(tmp_path, capsys):
    source = tmp_path / "export"
    reciter = source / "reciter"
    reciter.mkdir(parents=True)
    detailed = reciter / "detailed.json"
    history = reciter / "edit_history.jsonl"
    detailed.write_text(json.dumps(document()), encoding="utf-8")
    history.write_text(json.dumps(batch()), encoding="utf-8")
    original = (detailed.read_bytes(), history.read_bytes())
    files_before = set(tmp_path.rglob("*"))
    assert backfill.main(["--input", str(source)]) == 0
    assert json.loads(capsys.readouterr().out)["mode"] == "dry-run"
    assert set(tmp_path.rglob("*")) == files_before
    target = tmp_path / "staged"
    assert backfill.main(["--input", str(source), "--apply", "--output", str(target)]) == 0
    assert original == (detailed.read_bytes(), history.read_bytes())
    batches = [
        json.loads(line)
        for line in (target / "reciter/edit_history.jsonl").read_text().splitlines()
    ]
    assert batches[0] == batch()
    assert batches[1]["batch_type"] == "join_verdict_backfill"
    assert batches[1]["operations"][0]["patch"]["before"][0].get("join_verdicts") is None
    capsys.readouterr()
    assert backfill.main(["--input", str(target)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["reciters"]["reciter"]["counts"] == {"unchanged": 1}
    with pytest.raises(SystemExit):
        backfill.main(["--input", str(source), "--apply", "--output", str(target)])


def test_apply_validates_all_inputs_before_writing(tmp_path):
    source = tmp_path / "export"
    for slug in ("a", "z"):
        reciter = source / slug
        reciter.mkdir(parents=True)
        (reciter / "detailed.json").write_text(json.dumps(document()), encoding="utf-8")
    (source / "z/edit_history.jsonl").write_text(
        '{"batch_id": "bad", "unknown": true}', encoding="utf-8"
    )
    target = tmp_path / "staged"
    with pytest.raises(ValueError):
        backfill.main(["--input", str(source), "--apply", "--output", str(target)])
    assert not target.exists()
