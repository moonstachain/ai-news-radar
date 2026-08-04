from __future__ import annotations

import json
from pathlib import Path

from scripts.build_radar_overviews import CORE_FILES, build, coverage


ROOT = Path(__file__).resolve().parents[1]


def test_coverage_thresholds_are_independent_from_freshness():
    assert coverage(8, 10)["status"] == "healthy"
    assert coverage(7, 10)["status"] == "limited"
    assert coverage(4, 10)["status"] == "critical"


def test_build_writes_version_bound_overviews_and_manifest(tmp_path):
    source = ROOT / "data"
    for name in CORE_FILES:
        (tmp_path / name).write_bytes((source / name).read_bytes())

    manifest = build(tmp_path)
    news = json.loads((tmp_path / "news-overview.json").read_text(encoding="utf-8"))
    business = json.loads((tmp_path / "business-overview.json").read_text(encoding="utf-8"))
    persisted = json.loads((tmp_path / "snapshot-manifest.json").read_text(encoding="utf-8"))

    assert persisted == manifest
    assert news["snapshot_id"] == manifest["snapshot_id"]
    assert business["snapshot_id"] == manifest["snapshot_id"]
    assert len(news["top_stories"]) == 3
    assert business["coverage"]["status"] == "limited"
    assert manifest["files"]["news-overview.json"]["bytes"] > 0
    assert "snapshot-manifest.json" not in manifest["files"]


def test_snapshot_id_changes_when_core_data_changes(tmp_path):
    source = ROOT / "data"
    for name in CORE_FILES:
        (tmp_path / name).write_bytes((source / name).read_bytes())
    first = build(tmp_path)["snapshot_id"]
    payload = json.loads((tmp_path / "latest-24h.json").read_text(encoding="utf-8"))
    payload["total_items"] += 1
    (tmp_path / "latest-24h.json").write_text(json.dumps(payload), encoding="utf-8")
    second = build(tmp_path)["snapshot_id"]
    assert first != second
