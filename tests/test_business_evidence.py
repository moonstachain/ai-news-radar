from datetime import datetime, timezone

from scripts.update_business_evidence import (
    SOURCES,
    build_case_bank,
    build_clusters,
    match_tags,
    match_yuanli_tags,
    score_signal,
)


def test_business_tags_map_opc_signal():
    source = next(item for item in SOURCES if item.source_id == "indie_hackers")
    text = "Solo founder builds an AI micro-SaaS with one-person company economics and subscription revenue"

    assert "OPC" in match_tags(text, source)
    assert "profit_container" in match_yuanli_tags(text)


def test_score_rewards_concrete_ai_business_model():
    source = next(item for item in SOURCES if item.source_id == "yc_blog")
    now = datetime.now(tz=timezone.utc)

    total, breakdown, opc_fit, case_score = score_signal(
        source,
        "How a solo founder built an AI agent workflow startup to $1M ARR",
        "Founder case study with pricing, revenue, customers, workflow automation, and go-to-market lessons.",
        now,
        now,
    )

    assert total >= 55
    assert breakdown["business_model_value"] > 0
    assert opc_fit > 0
    assert case_score > 0


def test_clusters_and_case_bank_from_sample_signals():
    source = next(item for item in SOURCES if item.source_id == "levelsio")
    now = datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z")
    from scripts.update_business_evidence import BusinessSignal

    signals = [
        BusinessSignal(
            signal_id=f"sig_{idx}",
            title=f"Solo founder AI SaaS case study {idx}",
            url=f"https://example.com/{idx}",
            source_id=source.source_id,
            source_name=source.name,
            published_at=now,
            captured_at=now,
            lane="opc",
            entities=["ExampleCo"],
            business_model_tags=["OPC", "Founder Case"],
            yuanli_tags=["yuanli_startup", "profit_container"],
            opc_fit_score=10,
            case_concreteness_score=12,
            total_score=72,
            score_breakdown={},
            summary="A concrete OPC case.",
        )
        for idx in range(3)
    ]

    clusters = build_clusters(signals)
    cases = build_case_bank(signals)

    assert clusters
    assert clusters[0]["lane"] == "opc"
    assert len(cases) == 3
