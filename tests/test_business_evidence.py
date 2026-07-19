import json
from datetime import datetime, timedelta, timezone

import pytest

from scripts.update_business_evidence import (
    DEFAULT_WINDOW_HOURS,
    SOURCES,
    BusinessSource,
    build_case_bank,
    build_clusters,
    fetch_feed,
    fetch_page_fallback,
    fetch_page_detail_source,
    fetch_sitemap_source,
    match_tags,
    match_yuanli_tags,
    parse_time,
    publication_page_metadata,
    run,
    score_signal,
)


class FakeResponse:
    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.content = text.encode("utf-8")
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, responses: dict[str, FakeResponse]):
        self.responses = responses

    def get(self, url: str, timeout: int):
        return self.responses[url]


def sample_source(*, lane: str = "ai_commercialization") -> BusinessSource:
    return BusinessSource(
        source_id="sample",
        name="Sample First Party",
        homepage_url="https://example.com/news",
        feed_url="https://example.com/feed.xml",
        lane=lane,
        authority_tier="tier_1",
    )


def rss_item(*, published: str | None) -> str:
    published_xml = f"<pubDate>{published}</pubDate>" if published is not None else ""
    return f"""
    <rss><channel><item>
      <title>AI agent workflow startup reaches 100 enterprise customers</title>
      <link>https://example.com/current-story</link>
      <description>Founder revenue pricing workflow case study with customer evidence.</description>
      {published_xml}
    </item></channel></rss>
    """


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


def test_default_window_is_strictly_24_hours():
    assert DEFAULT_WINDOW_HOURS == 24


def test_output_contract_rejects_a_non_24_hour_window(tmp_path):
    with pytest.raises(ValueError, match="requires window_hours=24"):
        run(tmp_path, window_hours=72, max_items=10, max_per_source=2)


def test_parse_time_does_not_substitute_capture_time():
    assert parse_time(None) is None
    assert parse_time("not-a-real-date") is None


def test_feed_missing_timestamp_is_dropped_and_fail_closed():
    source = sample_source()
    now = datetime(2026, 7, 19, 8, 0, tzinfo=timezone.utc)
    session = FakeSession({source.feed_url: FakeResponse(rss_item(published=None))})

    signals, status = fetch_feed(session, source, now, now - timedelta(hours=24), 10)

    assert signals == []
    assert status["ok"] is False
    assert status["quality_status"] == "unverified_timestamp"
    assert status["timestamp_skips"]["missing_timestamp"] == 1


def test_invalid_and_future_feed_timestamps_are_dropped():
    source = sample_source()
    now = datetime(2026, 7, 19, 8, 0, tzinfo=timezone.utc)
    invalid_session = FakeSession({source.feed_url: FakeResponse(rss_item(published="not-a-date"))})
    future_session = FakeSession({source.feed_url: FakeResponse(rss_item(published="Sun, 19 Jul 2026 09:00:00 GMT"))})

    invalid_signals, invalid_status = fetch_feed(invalid_session, source, now, now - timedelta(hours=24), 10)
    future_signals, future_status = fetch_feed(future_session, source, now, now - timedelta(hours=24), 10)

    assert invalid_signals == []
    assert invalid_status["timestamp_skips"]["invalid_timestamp"] == 1
    assert future_signals == []
    assert future_status["timestamp_skips"]["future_timestamp"] == 1


def test_startup_and_opc_items_outside_window_are_dropped():
    now = datetime(2026, 7, 19, 8, 0, tzinfo=timezone.utc)
    old_date = "Fri, 17 Jul 2026 07:59:00 GMT"
    for lane in ("startup_vc", "opc"):
        source = sample_source(lane=lane)
        session = FakeSession({source.feed_url: FakeResponse(rss_item(published=old_date))})

        signals, status = fetch_feed(session, source, now, now - timedelta(hours=24), 10)

        assert signals == []
        assert status["timestamp_skips"]["outside_window"] == 1


def test_page_fallback_without_local_structured_time_fails_closed():
    source = sample_source()
    now = datetime(2026, 7, 19, 8, 0, tzinfo=timezone.utc)
    html = """
    <html><body><article>
      <a href="/story">AI agent workflow startup reaches 100 enterprise customers</a>
      <p>Founder revenue pricing workflow case study.</p>
    </article></body></html>
    """
    session = FakeSession({source.homepage_url: FakeResponse(html)})

    signals, skips = fetch_page_fallback(session, source, now, now - timedelta(hours=24), 10)

    assert signals == []
    assert skips["missing_timestamp"] == 1


def test_page_fallback_accepts_only_local_time_inside_window():
    source = sample_source()
    now = datetime(2026, 7, 19, 8, 0, tzinfo=timezone.utc)
    html = """
    <html><body><article>
      <time datetime="2026-07-19T07:30:00Z">30 minutes ago</time>
      <a href="/story">AI agent workflow startup reaches 100 enterprise customers</a>
      <p>Founder revenue pricing workflow case study.</p>
    </article></body></html>
    """
    session = FakeSession({source.homepage_url: FakeResponse(html)})

    signals, skips = fetch_page_fallback(session, source, now, now - timedelta(hours=24), 10)

    assert len(signals) == 1
    assert sum(skips.values()) == 0
    assert signals[0].published_at == "2026-07-19T07:30:00Z"
    assert signals[0].timestamp_basis == "page_structured_time"
    assert signals[0].transport_mode == "page_fallback"


def test_feed_uses_first_working_official_candidate():
    source = BusinessSource(
        source_id="sample",
        name="Sample First Party",
        homepage_url="https://example.com/news",
        feed_url="https://example.com/legacy.xml",
        lane="ai_commercialization",
        authority_tier="tier_1",
        feed_candidates=("https://example.com/legacy.xml", "https://example.com/current.xml"),
        entry_hosts=("example.com",),
    )
    now = datetime(2026, 7, 19, 8, 0, tzinfo=timezone.utc)
    session = FakeSession(
        {
            "https://example.com/legacy.xml": FakeResponse("not found", 404),
            "https://example.com/current.xml": FakeResponse(
                rss_item(published="Sun, 19 Jul 2026 07:30:00 GMT")
            ),
        }
    )

    signals, status = fetch_feed(session, source, now, now - timedelta(hours=24), 10)

    assert len(signals) == 1
    assert status["ok"] is True
    assert status["selected_url"] == "https://example.com/current.xml"
    assert status["attempted_urls"] == [
        "https://example.com/legacy.xml",
        "https://example.com/current.xml",
    ]


def test_plaintext_feed_cross_check_conflict_fails_closed():
    source = BusinessSource(
        source_id="sample",
        name="Sample First Party",
        homepage_url="https://example.com/news",
        feed_url="http://feeds.example.com/current.xml",
        lane="ai_commercialization",
        authority_tier="tier_1",
        entry_hosts=("example.com",),
        entry_base_url="https://example.com/",
        require_entry_page_cross_check=True,
    )
    page_url = "https://example.com/current-story"
    now = datetime(2026, 7, 19, 8, 0, tzinfo=timezone.utc)
    session = FakeSession(
        {
            source.feed_url: FakeResponse(rss_item(published="Sun, 19 Jul 2026 07:30:00 GMT")),
            page_url: FakeResponse(article_html(page_url, "2026-07-19T07:31:00Z")),
        }
    )

    signals, status = fetch_feed(session, source, now, now - timedelta(hours=24), 10)

    assert signals == []
    assert status["ok"] is False
    assert status["quality_status"] == "unverified_timestamp"
    assert status["timestamp_skips"]["conflicted_timestamp"] == 1


def test_empty_feed_and_unverified_page_remain_failed():
    source = sample_source()
    now = datetime(2026, 7, 19, 8, 0, tzinfo=timezone.utc)
    session = FakeSession(
        {
            source.feed_url: FakeResponse("<rss><channel></channel></rss>"),
            source.homepage_url: FakeResponse("<html><body><a href='/story'>No timestamp here at all</a></body></html>"),
        }
    )

    signals, status = fetch_feed(session, source, now, now - timedelta(hours=24), 10)

    assert signals == []
    assert status["ok"] is False
    assert status["quality_status"] == "empty_feed"


def sitemap_xml(page_url: str, last_modified: str) -> str:
    return f"""
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>{page_url}</loc><lastmod>{last_modified}</lastmod></url>
    </urlset>
    """


def article_html(page_url: str, published: str | None) -> str:
    website = f'<script type="application/ld+json">{json.dumps({"@type": "WebSite", "url": "https://example.com"})}</script>'
    structured = (
        f'<script type="application/ld+json">{json.dumps({"headline": "AI agent workflow startup reaches 100 enterprise customers", "url": page_url, "datePublished": published})}</script>'
        if published is not None
        else ""
    )
    return f"""
    <html><head>
      <link rel="canonical" href="{page_url}">
      <meta property="og:title" content="AI agent workflow startup reaches 100 enterprise customers">
      {website}
      {structured}
    </head></html>
    """


def test_sitemap_mode_requires_page_bound_publication_time():
    sitemap_url = "https://example.com/sitemap.xml"
    page_url = "https://example.com/articles/current"
    source = BusinessSource(
        source_id="sample",
        name="Sample First Party",
        homepage_url="https://example.com/articles",
        feed_url="https://example.com/removed-feed.xml",
        lane="ai_commercialization",
        authority_tier="tier_1",
        capture_mode="sitemap",
        sitemap_urls=(sitemap_url,),
        entry_hosts=("example.com",),
        entry_path_pattern=r"^/articles/",
    )
    now = datetime(2026, 7, 19, 8, 0, tzinfo=timezone.utc)
    session = FakeSession(
        {
            sitemap_url: FakeResponse(sitemap_xml(page_url, "2026-07-19T07:45:00Z")),
            page_url: FakeResponse(article_html(page_url, "2026-07-19T07:30:00Z")),
        }
    )

    signals, status = fetch_sitemap_source(session, source, now, now - timedelta(hours=24), 10)

    assert len(signals) == 1
    assert status["ok"] is True
    assert signals[0].timestamp_basis == "page_structured_time"
    assert signals[0].transport_mode == "sitemap_page"

    unverified = FakeSession(
        {
            sitemap_url: FakeResponse(sitemap_xml(page_url, "2026-07-19T07:45:00Z")),
            page_url: FakeResponse(article_html(page_url, None)),
        }
    )
    missing_signals, missing_status = fetch_sitemap_source(
        unverified, source, now, now - timedelta(hours=24), 10
    )

    assert missing_signals == []
    assert missing_status["ok"] is False
    assert missing_status["timestamp_skips"]["unverified_page"] == 1


def test_next_frame_metadata_is_bound_to_requested_article_slug():
    page_url = "https://example.com/news/current-story"
    frame = "0:" + json.dumps(
        {
            "post": {
                "title": "AI agent workflow startup reaches 100 enterprise customers",
                "publishedOn": "2026-07-19T07:30:00Z",
                "slug": {"current": "current-story"},
                "relatedPosts": [
                    {"title": "Wrong related story", "publishedOn": "2026-07-19T07:59:00Z", "slug": {"current": "wrong"}}
                ],
            }
        }
    )
    push = json.dumps([1, frame])
    html = f"""
    <html><head><link rel="canonical" href="{page_url}"></head>
    <body><script>self.__next_f.push({push})</script></body></html>
    """

    metadata = publication_page_metadata(html.encode(), page_url, {"example.com"})

    assert metadata["title"] == "AI agent workflow startup reaches 100 enterprise customers"
    assert metadata["published"] == datetime(2026, 7, 19, 7, 30, tzinfo=timezone.utc)


def test_page_detail_mode_follows_listing_but_trusts_only_article_metadata():
    source = BusinessSource(
        source_id="sample",
        name="Sample First Party",
        homepage_url="https://example.com/",
        feed_url="https://example.com/removed.xml",
        lane="opc",
        authority_tier="tier_2",
        capture_mode="page_detail",
        entry_hosts=("example.com",),
        entry_path_pattern=r"^/post/",
    )
    page_url = "https://example.com/post/current"
    now = datetime(2026, 7, 19, 8, 0, tzinfo=timezone.utc)
    session = FakeSession(
        {
            source.homepage_url: FakeResponse(f"<html><body><a href='{page_url}'>Current post</a></body></html>"),
            page_url: FakeResponse(article_html(page_url, "2026-07-19T07:30:00Z")),
        }
    )

    signals, status = fetch_page_detail_source(session, source, now, now - timedelta(hours=24), 10)

    assert len(signals) == 1
    assert status["ok"] is True
    assert status["transport_mode"] == "page_detail"
