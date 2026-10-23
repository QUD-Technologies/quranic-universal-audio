"""``/api/seg/specials`` — read a reciter's specials and confirm a Basmala voice verdict."""

from __future__ import annotations

import json

import pytest

from services.db import repo_transitions

RECITER = "fixture_reciter"
_ORIGIN = {"Origin": "http://localhost"}
_DOC = {
    "schema_version": 1,
    "generated_at": "t",
    "chapters": {
        "1": [
            {
                "type": "Basmala",
                "start_ms": 300,
                "end_ms": 5200,
                "source": "fatiha_verse",
                "voice_z": 2.0,
                "voice_verdict": "needs_confirmation",
            }
        ]
    },
}


@pytest.fixture
def with_specials(tmp_path, tmp_reciter_dir):
    tmp_reciter_dir.install(RECITER, "112-ikhlas")
    path = tmp_path / "reciters" / RECITER / "specials.json"
    path.write_text(json.dumps(_DOC), "utf-8")
    return path


def _confirm(client, **body):
    payload = {"chapter": 1, "start_ms": 300, "same_voice": False, **body}
    return client.post(f"/api/seg/specials/{RECITER}/voice", json=payload, headers=_ORIGIN)


def test_get_returns_the_chapters(with_specials, flask_client):
    resp = flask_client.get(f"/api/seg/specials/{RECITER}")
    assert resp.status_code == 200
    assert resp.get_json()["chapters"]["1"][0]["voice_verdict"] == "needs_confirmation"


def test_get_without_a_file_is_empty(tmp_reciter_dir, flask_client):
    tmp_reciter_dir.install(RECITER, "112-ikhlas")
    assert flask_client.get(f"/api/seg/specials/{RECITER}").get_json() == {"chapters": {}}


def test_maintainer_confirm_is_written_and_audited(with_specials, signed_in_client):
    client, _ = signed_in_client(hf_user_id="m-1", login="maint", role="maintainer")
    assert _confirm(client).status_code == 200
    saved = json.loads(with_specials.read_text("utf-8"))["chapters"]["1"][0]
    assert saved["voice_confirmed"] is False
    events = [t["event"] for t in repo_transitions.for_slug(RECITER)]
    assert "specials.voice_confirmed" in events


def test_contributor_cannot_confirm(with_specials, signed_in_client):
    client, _ = signed_in_client(hf_user_id="c-1", login="contrib", role="contributor")
    assert _confirm(client).status_code == 403
    assert "voice_confirmed" not in json.loads(with_specials.read_text("utf-8"))["chapters"]["1"][0]


def test_confirming_an_unknown_entry_is_404(with_specials, signed_in_client):
    client, _ = signed_in_client(hf_user_id="m-1", login="maint", role="maintainer")
    assert _confirm(client, start_ms=999).status_code == 404


def test_a_bad_body_is_400(with_specials, signed_in_client):
    client, _ = signed_in_client(hf_user_id="m-1", login="maint", role="maintainer")
    assert _confirm(client, same_voice="yes").status_code == 400
