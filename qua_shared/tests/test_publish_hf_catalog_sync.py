from __future__ import annotations

from pathlib import Path
from typing import Any

from qua_jobs import publish_hf
from qua_shared.hf_dataset_catalog import HfDatasetCatalogStats


def test_publish_hf_syncs_catalog_and_card_after_split(monkeypatch, tmp_path):
    bucket = tmp_path / "bucket"
    db_dir = bucket / "db"
    db_dir.mkdir(parents=True)
    db_path = db_dir / "inspector.db"
    db_path.write_bytes(b"sqlite placeholder")

    called: dict[str, Any] = {}
    stats = HfDatasetCatalogStats(
        timestamped_recitations=1,
        timestamped_riwayat=1,
        timestamped_seconds=360_000,
    )

    def fake_splits_by_config(*, repo_id: str, token: str | None):
        called["splits"] = (repo_id, token)
        return {"hafs_an_asim": ["maher_al_meaqli"]}

    def fake_push_catalog_dataset(
        *,
        repo_id: str,
        db_path: Path,
        token: str | None,
        published_slugs: set[str],
        now_iso: str | None = None,
    ):
        called["push"] = (repo_id, db_path, token, published_slugs)
        called["now_iso"] = now_iso
        return stats

    def fake_render_dataset_card(*, template_path: Path, splits_by_config, stats):
        called["render"] = (template_path, splits_by_config, stats)
        return "RENDERED CARD"

    def fake_upload_dataset_card(*, repo_id: str, content: str, token: str | None):
        called["upload"] = (repo_id, content, token)

    def fake_sync_dataset_assets(*, repo_id, assets, remove=(), token):
        called["assets"] = (repo_id, assets, remove, token)

    # The staged job code root: the DK script is built from the pack at staging time.
    code_root = tmp_path / "code"
    (code_root / "data").mkdir(parents=True)
    (code_root / "data/digital_khatt_v2_script.json").write_bytes(b'{"1:1:1": {}}')
    font = code_root / "data/fonts/DigitalKhattV2.otf"
    font.parent.mkdir(parents=True)
    font.write_bytes(b"OTTO font")

    monkeypatch.setenv("INSPECTOR_BUCKET_MOUNT", str(bucket))
    monkeypatch.setenv("INSPECTOR_CODE_DIR", str(code_root))
    monkeypatch.setenv("HF_TOKEN", "hf_test")
    monkeypatch.setattr(
        "qua_shared.hf_dataset_catalog.hub_published_splits_by_config",
        fake_splits_by_config,
    )
    monkeypatch.setattr(
        "qua_shared.hf_dataset_catalog.push_catalog_dataset",
        fake_push_catalog_dataset,
    )
    monkeypatch.setattr(
        "qua_shared.hf_dataset_catalog.render_dataset_card",
        fake_render_dataset_card,
    )
    monkeypatch.setattr(
        "qua_shared.hf_dataset_catalog.upload_dataset_card",
        fake_upload_dataset_card,
    )
    monkeypatch.setattr(
        "qua_shared.hf_dataset_catalog.sync_dataset_assets",
        fake_sync_dataset_assets,
    )

    publish_hf._sync_dataset_catalog_and_card("owner/dataset")

    assert called["splits"] == ("owner/dataset", "hf_test")
    assert called["push"] == (
        "owner/dataset",
        db_path,
        "hf_test",
        {"maher_al_meaqli"},
    )
    assert called["render"] == (
        Path(publish_hf._REPO_ROOT) / "docs" / "templates" / "hf_dataset_card.md",
        {"hafs_an_asim": ["maher_al_meaqli"]},
        stats,
    )
    assert called["upload"] == ("owner/dataset", "RENDERED CARD", "hf_test")

    repo_id, assets, remove, token = called["assets"]
    assert repo_id == "owner/dataset"
    assert assets == {
        "digital_khatt_v2_script.json": b'{"1:1:1": {}}',
        "DigitalKhattV2.otf": b"OTTO font",
    }
    assert remove == ("letter_vocab_hafs_qpc.csv", "qpc_hafs.json")
    assert token == "hf_test"
