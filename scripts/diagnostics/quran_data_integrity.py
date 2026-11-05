#!/usr/bin/env python3
"""Integrity gate for the Hafs reference-data swap (bundled JSON -> talqeen-quran-data SDK).

Captures every output that the retired ``data/surah_info.json``, ``data/qpc_hafs.json`` and
``data/digital_khatt_v2_script.json`` fed, from whichever checkout runs it, then compares two
captures:

    python scripts/diagnostics/quran_data_integrity.py capture OUT_DIR
    python scripts/diagnostics/quran_data_integrity.py compare BEFORE_DIR AFTER_DIR [--report F]

Run ``capture`` once on the old code (origin/main) and once on the new; ``compare`` exits 1
on any unexpected difference. Expectations:

* ``/api/surah-info``, ``get_word_counts``, cut_release's ``surah_info.json`` and its static
  ref digest for it: byte-identical.
* ``/api/static/quran-refs.json``, ``/api/ts/resource/digital_khatt`` and cut_release's
  ``digital_khatt_v2_script.json``: deep-equal (same keys, values and ids). Bytes may differ
  only in key order: the new data is in reading order, the old file in its export order.
* ``/api/ts/resource/qpc_hafs``: same keys and per-word fields; only ``text`` changes (it is
  Digital Khatt now), on exactly ``EXPECTED_QPC_TEXT_DIFFS`` words.
* ``/api/ts/resource/digital_khatt_<layout>`` (new; a layout with its own stop edition, i.e.
  1405): same keys, ids and markers as ``digital_khatt``; words differ only in waqf marks.
* cut_release's ``static_refs`` digests: ``surah_info.json`` and the font identical. The
  ``digital_khatt_v2_script.json`` digest is reported: its bytes change (reading order), so
  the first cut_release after the swap reports ``static_refs_changed`` and bumps once.
* Stop signs (``word_has_stop`` over every word) and validation of the offline segment
  fixtures are reported, not gated: stop changes are expected (QPC -> DK v2 stops).

Nothing here mutates the bucket: capture runs the app against a throwaway filesystem
backend.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXPECTED_QPC_TEXT_DIFFS = 23_638
LAYOUT_DK_PREFIX = "digital_khatt_"
FIXTURES = ("112-ikhlas", "113-falaq", "synthetic-classifier", "synthetic-structural")
DK_FONT = "inspector/frontend/public/fonts/DigitalKhattV2.otf"
#: Every Quranic waqf mark, U+06D6..U+06DB (STOP_SIGNS leaves out U+06D9 and U+06DB).
WAQF_MARKS = {chr(cp) for cp in range(0x06D6, 0x06DC)}

BYTES = "bytes"
DEEP = "deep"
QPC = "qpc-text"
#: output file -> expectation
GATED = {
    "api_surah_info.json": BYTES,
    "word_counts.json": BYTES,
    "quran_refs.json": DEEP,
    "ts_resource_digital_khatt.json": DEEP,
    "ts_resource_qpc_hafs.json": QPC,
    "cut_release/surah_info.json": BYTES,
    "cut_release/digital_khatt_v2_script.json": DEEP,
    "cut_release/static_refs.surah_info.json": BYTES,
    "cut_release/font.sha256": BYTES,
}


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------


def _boot(scratch: Path):
    """Import the Flask app against a throwaway filesystem backend."""
    os.environ.setdefault("INSPECTOR_SESSION_SECRET", "0" * 64)
    os.environ["INSPECTOR_BACKEND"] = "filesystem"
    os.environ["INSPECTOR_FILESYSTEM_ROOT"] = str(scratch)
    os.environ["INSPECTOR_AUTO_MOUNT"] = "0"
    sys.path[:0] = [str(ROOT / "inspector"), str(ROOT)]
    os.chdir(ROOT / "inspector")
    from app import app  # noqa: PLC0415 - must follow the env + path setup

    app.config["TESTING"] = True
    return app.test_client()


def _get(client, url: str) -> bytes:
    res = client.get(url)
    if res.status_code != 200:
        raise SystemExit(f"GET {url} -> {res.status_code}: {res.data[:200]!r}")
    return res.data


def _job_code_root(scratch: Path) -> Path:
    """The code root a cut_release job reads: the repo (old) or what staging uploads (new)."""
    from services.admin.jobs import base  # noqa: PLC0415

    staged = getattr(base, "staged_reference_files", None)
    if staged is None:
        return ROOT
    root = scratch / "job-code"
    for rel, body in staged().items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(body)
    (root / DK_FONT).parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / DK_FONT, root / DK_FONT)
    return root


def _capture_cut_release(out: Path, scratch: Path) -> None:
    from qua_jobs import cut_release  # noqa: PLC0415

    code_root = _job_code_root(scratch)
    script, font = cut_release._load_digital_khatt_assets(code_root)
    refs_dir = code_root / "data"
    # The Hafs assets cut_release hashes alongside surah_info.json (its ``digital_khatt_assets``).
    static = cut_release._hash_static_refs(
        refs_dir,
        {
            cut_release.DIGITAL_KHATT_SCRIPT_FILENAME: script,
            cut_release.DIGITAL_KHATT_FONT_FILENAME: font,
        },
    )
    target = out / "cut_release"
    target.mkdir(parents=True, exist_ok=True)
    (target / "surah_info.json").write_bytes((refs_dir / "surah_info.json").read_bytes())
    (target / "digital_khatt_v2_script.json").write_bytes(script)
    (target / "static_refs.surah_info.json").write_bytes(
        json.dumps(static["surah_info.json"], sort_keys=True).encode()
    )
    (target / "static_refs.json").write_text(json.dumps(static, sort_keys=True, indent=1))
    (target / "font.sha256").write_text(hashlib.sha256(font).hexdigest())


def _capture_layout_resources(out: Path, client) -> None:
    """Every ``digital_khatt_<layout>`` resource the manifest advertises (none before)."""
    manifest = json.loads(gzip.decompress(_get(client, "/api/ts/manifest")))
    keys = sorted(k for k in manifest.get("resources", {}) if k.startswith(LAYOUT_DK_PREFIX))
    for key in keys:
        body = gzip.decompress(_get(client, f"/api/ts/resource/{key}"))
        (out / f"ts_resource_{key}.json").write_bytes(body)
    (out / "layout_resources.json").write_text(json.dumps(keys))


def _capture_stops(out: Path, client) -> None:
    """Every word's stop status and the stop signs of the text it is read from."""
    from constants import STOP_SIGNS  # noqa: PLC0415
    from services.storage.data_loader import get_word_counts, word_has_stop  # noqa: PLC0415

    qpc = json.loads(gzip.decompress(_get(client, "/api/ts/resource/qpc_hafs")))
    stops: dict[str, dict] = {}
    for (surah, ayah), n in sorted(get_word_counts().items()):
        for word in range(1, n + 1):
            ref = f"{surah}:{ayah}:{word}"
            text = qpc.get(ref, {}).get("text", "")
            signs = "".join(sorted(STOP_SIGNS & set(text)))
            has = word_has_stop(surah, ayah, word)
            if has or signs:
                stops[ref] = {"stop": has, "signs": signs}
    (out / "stops.json").write_text(json.dumps(stops, ensure_ascii=False, indent=1))


def _capture_validation(out: Path) -> None:
    """Validation detail lists + missing words (``word_has_stop``'s consumer) per fixture."""
    import copy  # noqa: PLC0415

    from services.storage.data_loader import get_word_counts  # noqa: PLC0415
    from services.validation._missing import _build_missing_words  # noqa: PLC0415
    from services.validation.detail import _build_detail_lists  # noqa: PLC0415

    fixtures = ROOT / "inspector" / "tests" / "fixtures" / "segments"
    counts = get_word_counts()
    result = {}
    for name in FIXTURES:
        doc = json.loads((fixtures / f"{name}.detailed.json").read_text(encoding="utf-8"))
        detail = _build_detail_lists(copy.deepcopy(doc["entries"]), False, counts)
        missing = _build_missing_words(detail["verse_segments"], counts, detail["sequence_gaps"])
        detail.pop("verse_segments")
        result[name] = {"missing_words": missing, "detail": detail}
    (out / "validation.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1, sort_keys=True, default=str)
    )


def capture(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix="quran-integrity-"))
    try:
        client = _boot(scratch)
        from services.storage.data_loader import get_word_counts  # noqa: PLC0415

        (out / "api_surah_info.json").write_bytes(_get(client, "/api/surah-info"))
        counts = {f"{s}:{a}": n for (s, a), n in get_word_counts().items()}
        (out / "word_counts.json").write_text(json.dumps(counts))
        (out / "quran_refs.json").write_bytes(_get(client, "/api/static/quran-refs.json"))
        for key in ("digital_khatt", "qpc_hafs"):
            body = gzip.decompress(_get(client, f"/api/ts/resource/{key}"))
            (out / f"ts_resource_{key}.json").write_bytes(body)
        _capture_layout_resources(out, client)
        _capture_cut_release(out, scratch)
        _capture_stops(out, client)
        _capture_validation(out)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    print(f"captured -> {out}")


# ---------------------------------------------------------------------------
# Compare
# ---------------------------------------------------------------------------


def _qpc_check(before: dict, after: dict) -> tuple[bool, str]:
    if before.keys() != after.keys():
        return False, f"key sets differ ({len(before)} vs {len(after)})"
    other = [k for k in before if {**before[k], "text": ""} != {**after[k], "text": ""}]
    if other:
        return False, f"{len(other)} words differ outside text, e.g. {other[:3]}"
    changed = sum(1 for k in before if before[k]["text"] != after[k]["text"])
    ok = changed == EXPECTED_QPC_TEXT_DIFFS
    return ok, f"{changed} words' text differs (expected {EXPECTED_QPC_TEXT_DIFFS})"


def _compare_one(kind: str, before: bytes, after: bytes) -> tuple[bool, str]:
    if before == after:
        return True, "byte-identical"
    if kind == BYTES:
        return False, "bytes differ"
    old, new = json.loads(before), json.loads(after)
    if kind == DEEP:
        return (
            (True, "deep-equal (key order differs)") if old == new else (False, "content differs")
        )
    return _qpc_check(old, new)


def _sign_changes(before: dict[str, str], after: dict[str, str]) -> dict[str, list[dict]]:
    """Classify per-word sign strings (``""`` = none): lost, gained, or a different sign."""
    out: dict[str, list[dict]] = {"lose": [], "gain": [], "change_sign": []}
    for ref in sorted(before.keys() | after.keys(), key=_ref_key):
        old, new = before.get(ref, ""), after.get(ref, "")
        kind = (
            "lose"
            if old and not new
            else "gain"
            if new and not old
            else "change_sign"
            if old != new
            else None
        )
        if kind:
            out[kind].append({"ref": ref, "qpc": old, "dk": new})
    return out


def _stop_report(before: dict, after: dict) -> dict:
    """``word_has_stop`` (``constants.STOP_SIGNS``) changes."""
    return _sign_changes(
        {r: e["signs"] for r, e in before.items() if e["stop"]},
        {r: e["signs"] for r, e in after.items() if e["stop"]},
    )


def _waqf_report(before: dict, after: dict) -> dict:
    """Changes over every waqf mark U+06D6..U+06DB in the word text (the doc's figure)."""

    def signs(words: dict) -> dict[str, str]:
        out = {r: "".join(sorted(WAQF_MARKS & set(e["text"]))) for r, e in words.items()}
        return {r: v for r, v in out.items() if v}

    return _sign_changes(signs(before), signs(after))


def _strip_waqf(text: str) -> str:
    return "".join(ch for ch in text if ch not in WAQF_MARKS)


def _layout_check(default: dict, layout: dict) -> tuple[bool, str, dict]:
    """A stop-edition script vs the default one: only waqf marks in words may differ."""
    if list(default) != list(layout):
        return False, "keys differ from digital_khatt", {}
    other = [k for k in default if {**default[k], "text": ""} != {**layout[k], "text": ""}]
    if other:
        return False, f"{len(other)} entries differ outside text, e.g. {other[:3]}", {}
    changed = [k for k in default if default[k]["text"] != layout[k]["text"]]
    beyond = [
        k for k in changed if _strip_waqf(default[k]["text"]) != _strip_waqf(layout[k]["text"])
    ]
    if beyond:
        return False, f"{len(beyond)} words differ beyond waqf marks, e.g. {beyond[:3]}", {}
    marks = _waqf_report(default, layout)
    counts = ", ".join(f"{len(v)} {k.replace('_', ' ')}" for k, v in marks.items())
    return True, f"{len(changed)} words differ, waqf marks only ({counts})", marks


def _compare_layout_resources(after: Path, report: dict) -> int:
    """Gate every ``digital_khatt_<layout>`` resource of the new capture."""
    keys = json.loads((after / "layout_resources.json").read_text(encoding="utf-8"))
    default = json.loads((after / "ts_resource_digital_khatt.json").read_text(encoding="utf-8"))
    failures = 0
    for key in keys:
        name = f"ts_resource_{key}.json"
        body = json.loads((after / name).read_text(encoding="utf-8"))
        ok, detail, marks = _layout_check(default, body)
        failures += not ok
        report["gated"][name] = {"expect": "stop-edition", "ok": ok, "detail": detail}
        report[f"stops_dk_default_to_{key.removeprefix(LAYOUT_DK_PREFIX)}"] = marks
        print(f"{'PASS' if ok else 'FAIL'}  {name:<45} {detail}")
    return failures


def _report_static_refs(before: Path, after: Path, report: dict) -> None:
    """cut_release's static_refs digests: which change and so bump the next release."""
    old = json.loads((before / "cut_release" / "static_refs.json").read_text(encoding="utf-8"))
    new = json.loads((after / "cut_release" / "static_refs.json").read_text(encoding="utf-8"))
    changed = sorted(k for k in old.keys() | new.keys() if old.get(k) != new.get(k))
    report["static_refs_changed"] = {
        k: {"before": old.get(k), "after": new.get(k)} for k in changed
    }
    print(
        f"INFO  cut_release static_refs digests changed: {changed or 'none'}"
        + (" -> the next cut_release reports static_refs_changed and bumps once" if changed else "")
    )


def _ref_key(ref: str) -> tuple[int, ...]:
    return tuple(int(x) for x in ref.split(":"))


def compare(before: Path, after: Path, report_path: Path | None) -> int:
    failures = 0
    report: dict = {"gated": {}}
    for name, kind in GATED.items():
        ok, detail = _compare_one(kind, (before / name).read_bytes(), (after / name).read_bytes())
        failures += not ok
        report["gated"][name] = {"expect": kind, "ok": ok, "detail": detail}
        print(f"{'PASS' if ok else 'FAIL'}  {name:<45} {detail}")
    failures += _compare_layout_resources(after, report)
    _report_static_refs(before, after, report)

    stops = _stop_report(
        json.loads((before / "stops.json").read_text(encoding="utf-8")),
        json.loads((after / "stops.json").read_text(encoding="utf-8")),
    )
    report["stops"] = stops
    waqf = _waqf_report(
        json.loads((before / "ts_resource_qpc_hafs.json").read_text(encoding="utf-8")),
        json.loads((after / "ts_resource_qpc_hafs.json").read_text(encoding="utf-8")),
    )
    report["waqf_marks"] = waqf
    for label, changes in (("word_has_stop", stops), ("all waqf marks U+06D6-06DB", waqf)):
        counts = ", ".join(f"{len(v)} {k.replace('_', ' ')}" for k, v in changes.items())
        print(f"INFO  {label} QPC -> DK: {counts}")
    vb = json.loads((before / "validation.json").read_text(encoding="utf-8"))
    va = json.loads((after / "validation.json").read_text(encoding="utf-8"))
    changed = {name: {"before": vb[name], "after": va[name]} for name in vb if vb[name] != va[name]}
    report["validation_changes"] = changed
    issues = Counter({name: len(doc["missing_words"]) for name, doc in va.items()})
    print(
        f"INFO  validation on offline fixtures: {len(changed)} of {len(va)} changed "
        f"(missing-word issues after: {dict(issues)})"
    )
    if report_path:
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=1))
        print(f"report -> {report_path}")
    print("OK" if not failures else f"{failures} gated output(s) differ")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    cap = sub.add_parser("capture")
    cap.add_argument("out", type=Path)
    cmp_ = sub.add_parser("compare")
    cmp_.add_argument("before", type=Path)
    cmp_.add_argument("after", type=Path)
    cmp_.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.cmd == "capture":
        capture(args.out.resolve())
        return 0
    return compare(args.before.resolve(), args.after.resolve(), args.report)


if __name__ == "__main__":
    raise SystemExit(main())
