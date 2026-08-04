#!/usr/bin/env python3
"""Build lightweight, checksum-bound overview payloads for the Radar UI."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
CORE_FILES = (
    "latest-24h.json",
    "latest-24h-all.json",
    "daily-brief.json",
    "stories-merged.json",
    "source-status.json",
    "waytoagi-7d.json",
    "business-latest-24h.json",
    "business-daily-brief.json",
    "business-stories-merged.json",
    "business-source-status.json",
    "business-case-bank.json",
    "business-source-catalog.json",
)


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return payload


def payload_generated_at(path: Path) -> str | None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload.get("generated_at") if isinstance(payload, dict) else None


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_time(value: str | None) -> datetime:
    if not value:
        raise ValueError("generated_at is required")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def coverage(successful: int, total: int) -> dict[str, Any]:
    ratio = successful / total if total else 0.0
    if ratio >= 0.8:
        status = "healthy"
    elif ratio >= 0.5:
        status = "limited"
    else:
        status = "critical"
    return {
        "successful": successful,
        "total": total,
        "failed": max(0, total - successful),
        "ratio": round(ratio, 4),
        "status": status,
    }


def compact_story(item: dict[str, Any]) -> dict[str, Any]:
    compact = {
        key: item.get(key)
        for key in (
            "story_id",
            "title",
            "url",
            "primary_url",
            "source",
            "source_name",
            "source_count",
            "source_names",
            "importance_score",
            "importance_label",
            "category",
            "reasons",
            "latest_at",
            "earliest_at",
            "sources",
        )
        if item.get(key) is not None
    }
    compact["confidence"] = "multi-source" if int(item.get("source_count") or 0) > 1 else "single-source"
    return compact


def build_news_overview(
    snapshot_id: str,
    latest: dict[str, Any],
    brief: dict[str, Any],
    stories: dict[str, Any],
    status: dict[str, Any],
) -> dict[str, Any]:
    items = latest.get("items") or []
    sites = status.get("sites") or []
    successful = int(status.get("successful_sites") or 0)
    total_sources = len(sites)
    section_counts = Counter(
        str(item.get("site_name") or item.get("category") or "Other") for item in items
    )
    high_priority = sum(
        1
        for item in items
        if float(item.get("ai_score") or item.get("importance_score") or 0) >= 0.8
    )
    top_stories = [compact_story(item) for item in (brief.get("items") or [])[:3]]
    generated_at = str(latest.get("generated_at") or brief.get("generated_at"))
    return {
        "schema_version": SCHEMA_VERSION,
        "snapshot_id": snapshot_id,
        "channel": "ai-news",
        "generated_at": generated_at,
        "window_hours": int(latest.get("window_hours") or 24),
        "decision": "优先追踪官方模型、产品与开发工具迁移；把单源判断和来源缺口视为待验证情报。",
        "metrics": {
            "signals": int(latest.get("total_items") or len(items)),
            "high_priority": high_priority,
            "briefs": int(brief.get("total_items") or len(brief.get("items") or [])),
            "stories": int(stories.get("total_stories") or len(stories.get("stories") or [])),
        },
        "coverage": coverage(successful, total_sources),
        "failed_sources": list(status.get("failed_sites") or []),
        "section_counts": dict(section_counts.most_common(8)),
        "top_stories": top_stories,
    }


def compact_business_brief(item: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item.get(key)
        for key in (
            "brief_id",
            "rank",
            "title",
            "judgment",
            "evidence_refs",
            "evidence_titles",
            "risk_level",
            "yuanli_mapping",
            "recommended_action",
        )
        if item.get(key) is not None
    }


def compact_cluster(item: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item.get(key)
        for key in (
            "cluster_id",
            "thesis",
            "lane",
            "source_count",
            "importance_score",
            "confidence",
            "yuanli_mapping",
            "why_it_matters",
            "counter_evidence",
            "recommended_action",
            "evidence_refs",
            "top_sources",
        )
        if item.get(key) is not None
    }


def build_business_overview(
    snapshot_id: str,
    latest: dict[str, Any],
    brief: dict[str, Any],
    stories: dict[str, Any],
    status: dict[str, Any],
    cases: dict[str, Any],
) -> dict[str, Any]:
    brief_items = [compact_business_brief(item) for item in (brief.get("brief") or [])[:5]]
    clusters = [compact_cluster(item) for item in (stories.get("clusters") or [])[:6]]
    successful = int(status.get("successful_sources") or 0)
    total_sources = int(status.get("source_count") or len(status.get("sources") or []))
    generated_at = str(latest.get("generated_at") or brief.get("generated_at"))
    actions = [
        {
            "id": item.get("brief_id"),
            "label": f"Brief #{item.get('rank')}",
            "title": item.get("recommended_action"),
            "context": item.get("title"),
        }
        for item in brief_items[:3]
        if item.get("recommended_action")
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "snapshot_id": snapshot_id,
        "channel": "ai-business",
        "generated_at": generated_at,
        "window_hours": int(latest.get("window_hours") or 24),
        "decision": (
            clusters[0].get("thesis")
            if clusters
            else "Prioritize evidence with concrete economics, repeatable distribution, and explicit counter-signals."
        ),
        "metrics": {
            "signals": len(latest.get("items") or []),
            "briefs": len(brief.get("brief") or []),
            "clusters": len(stories.get("clusters") or []),
            "cases": len(cases.get("cases") or []),
        },
        "coverage": coverage(successful, total_sources),
        "brief": brief_items,
        "actions": actions,
        "clusters": clusters,
    }


def build(output_dir: Path) -> dict[str, Any]:
    missing = [name for name in CORE_FILES if not (output_dir / name).is_file()]
    if missing:
        raise FileNotFoundError(f"missing radar data files: {', '.join(missing)}")

    core_hashes = {name: sha256(output_dir / name) for name in CORE_FILES}
    fingerprint = json.dumps(core_hashes, sort_keys=True, separators=(",", ":")).encode()
    snapshot_id = hashlib.sha256(fingerprint).hexdigest()[:16]

    latest = read_json(output_dir / "latest-24h.json")
    brief = read_json(output_dir / "daily-brief.json")
    stories = read_json(output_dir / "stories-merged.json")
    status = read_json(output_dir / "source-status.json")
    business_latest = read_json(output_dir / "business-latest-24h.json")
    business_brief = read_json(output_dir / "business-daily-brief.json")
    business_stories = read_json(output_dir / "business-stories-merged.json")
    business_status = read_json(output_dir / "business-source-status.json")
    business_cases = read_json(output_dir / "business-case-bank.json")

    news_overview = build_news_overview(snapshot_id, latest, brief, stories, status)
    business_overview = build_business_overview(
        snapshot_id,
        business_latest,
        business_brief,
        business_stories,
        business_status,
        business_cases,
    )
    write_json(output_dir / "news-overview.json", news_overview)
    write_json(output_dir / "business-overview.json", business_overview)

    channel_times = {
        "ai-news": news_overview["generated_at"],
        "ai-business": business_overview["generated_at"],
    }
    generated_at = max(channel_times.values(), key=lambda value: parse_time(value))
    file_names = (*CORE_FILES, "news-overview.json", "business-overview.json")
    files = {
        name: {
            "sha256": sha256(output_dir / name),
            "bytes": (output_dir / name).stat().st_size,
            "generated_at": payload_generated_at(output_dir / name),
        }
        for name in file_names
    }
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "snapshot_id": snapshot_id,
        "generated_at": generated_at,
        "channel_generated_at": channel_times,
        "files": files,
        "checksums": {name: meta["sha256"] for name, meta in files.items()},
    }
    # The manifest is intentionally written last so clients never discover a
    # snapshot before all version-bound files exist.
    write_json(output_dir / "snapshot-manifest.json", manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("data"))
    args = parser.parse_args()
    manifest = build(args.output_dir)
    print(json.dumps({"snapshot_id": manifest["snapshot_id"], "generated_at": manifest["generated_at"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
