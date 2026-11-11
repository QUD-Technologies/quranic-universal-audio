"""``GET /api/ts/readings/<reciter>`` and the readings refresh on ``ts-refreshed``."""

from __future__ import annotations

from qua_shared.schemas import TsReadingOption, TsReadingRow, TsReadingsDoc, TsReadingVerse

DOC = TsReadingsDoc(
    slug="reciter_a",
    built_at="2026-10-10T00:00:00Z",
    rows=[
        TsReadingRow(
            selector="istifham_article",
            key="istifham_article/allah",
            texts=["ءَآللَّهُ"],
            options=[
                TsReadingOption(
                    option="ibdal", verses=[TsReadingVerse(surah=27, ayah=59, label="27:59")]
                ),
                TsReadingOption(
                    option="tashil", verses=[TsReadingVerse(surah=10, ayah=59, label="10:59")]
                ),
            ],
        )
    ],
)


def test_readings_serves_the_summary(flask_client, monkeypatch):
    from routes.timestamps import timestamps as ts_routes

    monkeypatch.setattr(ts_routes.ts_serve, "is_viewable", lambda *a, **k: True)
    monkeypatch.setattr(ts_routes.readings_service, "doc", lambda slug: DOC)

    res = flask_client.get("/api/ts/readings/reciter_a")

    assert res.status_code == 200
    assert res.headers["Cache-Control"] == "no-store"
    assert TsReadingsDoc.model_validate(res.get_json()) == DOC


def test_readings_hidden_reciter_is_404(flask_client, monkeypatch):
    from routes.timestamps import timestamps as ts_routes

    monkeypatch.setattr(ts_routes.ts_serve, "is_viewable", lambda *a, **k: False)
    monkeypatch.setattr(ts_routes.readings_service, "doc", lambda slug: DOC)

    assert flask_client.get("/api/ts/readings/reciter_a").status_code == 404


def test_readings_non_hafs_is_empty(flask_client, monkeypatch):
    from routes.timestamps import timestamps as ts_routes

    from services.reference import readings

    monkeypatch.setattr(ts_routes.ts_serve, "is_viewable", lambda *a, **k: True)
    monkeypatch.setattr(readings, "is_hafs", lambda slug: False)
    readings.invalidate()

    res = flask_client.get("/api/ts/readings/warsh_reciter")

    assert res.status_code == 200
    assert res.get_json()["rows"] == []


def test_ts_refreshed_rebuilds_readings(flask_client, monkeypatch):
    from services.reference import readings

    monkeypatch.setenv("INSPECTOR_WEBHOOK_SECRET", "s3cret")
    rebuilt = []
    monkeypatch.setattr(readings, "refresh_in_background", rebuilt.append)

    res = flask_client.post(
        "/api/admin/internal/ts-refreshed",
        json={"slug": "rec_unknown"},
        headers={"X-Inspector-Job-Secret": "s3cret"},
    )

    assert res.status_code == 200
    assert rebuilt == ["rec_unknown"]
