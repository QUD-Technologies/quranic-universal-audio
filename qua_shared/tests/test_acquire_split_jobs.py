"""The align pipeline's audio HF Jobs end-to-end on a local "bucket mount":
``acquire_audio`` (singles → chapter mp3 + peaks, combined → one slot mp3) and
``split_audio`` (slot → per-chapter windows). Real ffmpeg on a generated tone;
only the network fetch is replaced by a local copy."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from qua_jobs import acquire_audio, audio_io, split_audio

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not on PATH",
)

SLUG = "rec_x"
COMBINED = "https://drive.google.com/file/d/COMBINEDFILE01/view"
TONE_MS = 6000


@pytest.fixture
def mount(tmp_path, monkeypatch):
    tone = tmp_path / "tone.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={TONE_MS / 1000}",
            str(tone),
        ],
        check=True,
    )
    root = tmp_path / "bucket"
    monkeypatch.setenv("INSPECTOR_BUCKET_MOUNT", str(root))
    monkeypatch.setenv("SLUG", SLUG)
    monkeypatch.setenv("RUN_ID", "run-1")
    fetched: list[str] = []

    def fake_fetch(url, dest):
        fetched.append(url)
        shutil.copyfile(tone, dest)
        return dest

    monkeypatch.setattr(audio_io, "fetch", fake_fetch)
    manifest = root / "catalog" / "audio_manifest" / f"{SLUG}.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "chapters": {
                    "1": {"url": "u1", "source_url": COMBINED},
                    "2": {"url": "u2", "source_url": COMBINED},
                    "3": {"url": "https://cdn.example/3.mp3"},
                }
            }
        ),
        encoding="utf-8",
    )
    return root, fetched


def _reciter(root: Path) -> Path:
    return root / "reciters" / SLUG


def test_acquire_persists_singles_and_combined_slots_then_skips_on_rerun(mount):
    root, fetched = mount
    assert acquire_audio.main() == 0
    audio = _reciter(root) / "audio"
    assert sorted(p.name for p in audio.iterdir()) == ["3.mp3", "901.mp3"]
    assert (_reciter(root) / "peaks" / "3.json.gz").is_file()
    assert not (_reciter(root) / "peaks" / "901.json.gz").exists()

    report = json.loads((root / "staging" / SLUG / "run-1" / "acquire.json").read_text("utf-8"))
    assert report["failures"] == {}
    assert report["sources"]["901"]["chapters"] == [1, 2]
    assert abs(report["sources"]["901"]["duration_ms"] - TONE_MS) < 200
    assert report["chapters"]["3"]["bytes"] == (audio / "3.mp3").stat().st_size

    assert acquire_audio.main() == 0
    assert len(fetched) == 2  # the rerun fetched nothing
    rerun = json.loads((root / "staging" / SLUG / "run-1" / "acquire.json").read_text("utf-8"))
    assert rerun["chapters"]["3"]["skipped"] is True


def test_split_cuts_windows_and_removes_the_slot(mount):
    root, _ = mount
    assert acquire_audio.main() == 0
    plan = root / "staging" / SLUG / "run-1" / "split_plan.json"
    plan.write_text(
        json.dumps({"slots": {"901": {"chapters": {"1": [0, 2500], "2": [2500, 6000]}}}})
    )

    assert split_audio.main() == 0

    audio = _reciter(root) / "audio"
    assert sorted(p.name for p in audio.iterdir()) == ["1.mp3", "2.mp3", "3.mp3"]
    report = json.loads((root / "staging" / SLUG / "run-1" / "split.json").read_text("utf-8"))
    assert report["failures"] == {}
    assert report["cuts"]["2"]["offset_ms"] == 2500
    assert abs(report["cuts"]["1"]["duration_ms"] - 2500) < 150
    assert abs(report["cuts"]["2"]["duration_ms"] - 3500) < 150
    assert (_reciter(root) / "peaks" / "2.json.gz").is_file()


def test_split_reports_a_missing_slot_and_keeps_going(mount):
    root, _ = mount
    plan = root / "staging" / SLUG / "run-1" / "split_plan.json"
    plan.parent.mkdir(parents=True)
    plan.write_text(json.dumps({"slots": {"902": {"chapters": {"5": [0, 1000]}}}}))

    assert split_audio.main() == 1
    report = json.loads((root / "staging" / SLUG / "run-1" / "split.json").read_text("utf-8"))
    assert report["failures"] == {"5": "source slot 902 is missing from the bucket"}
