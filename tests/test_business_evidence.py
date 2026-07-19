import gzip
import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests
import scripts.update_business_evidence as business_evidence

from scripts.update_business_evidence import (
    DEFAULT_WINDOW_HOURS,
    SOURCES,
    BusinessSource,
    FetchBudget,
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
    safe_get,
    score_signal,
    sitemap_candidates,
)


class FakeResponse:
    def __init__(self, text: str | bytes, status_code: int = 200, headers: dict[str, str] | None = None):
        self.content = text if isinstance(text, bytes) else text.encode("utf-8")
        self.text = self.content.decode("utf-8", errors="replace")
        self.status_code = status_code
        self.headers = headers or {}
        self.closed = False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size: int):
        for offset in range(0, len(self.content), chunk_size):
            yield self.content[offset : offset + chunk_size]

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, responses: dict[str, FakeResponse]):
        self.responses = responses
        self.requested_urls: list[str] = []

    def get(self, url: str, timeout: int, **kwargs):
        self.requested_urls.append(url)
        return self.responses[url]


@pytest.fixture(autouse=True)
def _stable_public_dns(monkeypatch):
    monkeypatch.setattr(business_evidence, "resolve_public_addresses", lambda hostname: ("93.184.216.34",))


def sample_source(*, lane: str = "ai_commercialization") -> BusinessSource:
    return BusinessSource(
        source_id="sample",
        name="Sample First Party",
        homepage_url="https://example.com/news",
        feed_url="https://example.com/feed.xml",
        lane=lane,
        authority_tier="tier_1",
    )


def rss_item(*, published: str | None, link: str = "https://example.com/current-story") -> str:
    published_xml = f"<pubDate>{published}</pubDate>" if published is not None else ""
    return f"""
    <rss><channel><item>
      <title>AI agent workflow startup reaches 100 enterprise customers</title>
      <link>{link}</link>
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


def test_old_verified_feed_is_stale_not_a_green_success():
    source = sample_source()
    now = datetime(2026, 7, 19, 8, 0, tzinfo=timezone.utc)
    session = FakeSession(
        {source.feed_url: FakeResponse(rss_item(published="Thu, 19 Jul 2018 07:30:00 GMT"))}
    )

    signals, status = fetch_feed(session, source, now, now - timedelta(hours=24), 10)

    assert signals == []
    assert status["ok"] is False
    assert status["reachable"] is True
    assert status["verified"] is True
    assert status["fresh"] is False
    assert status["current"] is False
    assert status["quality_status"] == "stale_source"
    assert status["latest_verified_published_at"] == "2018-07-19T07:30:00Z"
    assert status["error"] == "latest_verified_item_exceeds_source_sla"


def test_verified_weekly_feed_can_be_fresh_without_a_24_hour_item():
    source = sample_source()
    now = datetime(2026, 7, 19, 8, 0, tzinfo=timezone.utc)
    session = FakeSession(
        {source.feed_url: FakeResponse(rss_item(published="Fri, 17 Jul 2026 07:30:00 GMT"))}
    )

    signals, status = fetch_feed(session, source, now, now - timedelta(hours=24), 10)

    assert signals == []
    assert status["ok"] is True
    assert status["fresh"] is True
    assert status["current"] is False
    assert status["quality_status"] == "no_current_items"


def test_business_source_ui_uses_explicit_quality_dimensions():
    html = Path("business.html").read_text(encoding="utf-8")

    assert 'source.current ? "ok" : source.verified && source.fresh ? "watch" : "bad"' in html
    assert 'reachable ${source.reachable ? "yes" : "no"}' in html
    assert 'verified ${source.verified ? "yes" : "no"}' in html
    assert 'fresh ${source.fresh ? "yes" : "no"}' in html
    assert '24h ${source.current ? "yes" : "no"}' in html


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
        f'<script type="application/ld+json">{json.dumps({"@type": "NewsArticle", "headline": "AI agent workflow startup reaches 100 enterprise customers", "url": page_url, "datePublished": published})}</script>'
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


def test_page_fallback_rejects_external_links_even_with_local_time():
    source = sample_source()
    now = datetime(2026, 7, 19, 8, 0, tzinfo=timezone.utc)
    html = """
    <html><body><article>
      <time datetime="2026-07-19T07:30:00Z">30 minutes ago</time>
      <a href="https://outside.example/story">AI agent workflow startup reaches 100 enterprise customers</a>
    </article></body></html>
    """
    session = FakeSession({source.homepage_url: FakeResponse(html)})

    signals, skips = fetch_page_fallback(session, source, now, now - timedelta(hours=24), 10)

    assert signals == []
    assert sum(skips.values()) == 0


def test_page_fallback_does_not_borrow_timestamp_from_sibling_story():
    source = sample_source()
    now = datetime(2026, 7, 19, 8, 0, tzinfo=timezone.utc)
    html = """
    <html><body><section>
      <div><a href="/undated">AI agent workflow startup reaches 100 enterprise customers</a></div>
      <div>
        <time datetime="2026-07-19T07:30:00Z">30 minutes ago</time>
        <a href="/dated">AI founder pricing case with verified revenue and customers</a>
      </div>
    </section></body></html>
    """
    session = FakeSession({source.homepage_url: FakeResponse(html)})

    signals, skips = fetch_page_fallback(session, source, now, now - timedelta(hours=24), 10)

    assert [signal.url for signal in signals] == ["https://example.com/dated"]
    assert skips["missing_timestamp"] == 1


def test_page_fallback_timestamp_binding_counts_short_sibling_links():
    source = sample_source()
    now = datetime(2026, 7, 19, 8, 0, tzinfo=timezone.utc)
    html = """
    <html><body><section>
      <time datetime="2026-07-19T07:30:00Z">30 minutes ago</time>
      <a href="/fresh">Fresh</a>
      <a href="/undated">AI agent workflow startup reaches 100 enterprise customers</a>
    </section></body></html>
    """
    session = FakeSession({source.homepage_url: FakeResponse(html)})

    signals, skips = fetch_page_fallback(session, source, now, now - timedelta(hours=24), 10)

    assert signals == []
    assert skips["missing_timestamp"] == 1


def test_page_fallback_article_container_does_not_exempt_related_links():
    source = sample_source()
    now = datetime(2026, 7, 19, 8, 0, tzinfo=timezone.utc)
    html = """
    <html><body><article>
      <time datetime="2026-07-19T07:30:00Z">30 minutes ago</time>
      <a href="/fresh">Fresh</a>
      <a href="/undated">AI agent workflow startup reaches 100 enterprise customers</a>
    </article></body></html>
    """
    session = FakeSession({source.homepage_url: FakeResponse(html)})

    signals, skips = fetch_page_fallback(session, source, now, now - timedelta(hours=24), 10)

    assert signals == []
    assert skips["missing_timestamp"] == 1


def test_plaintext_feed_article_url_is_upgraded_to_https_before_cross_check():
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
            source.feed_url: FakeResponse(
                rss_item(
                    published="Sun, 19 Jul 2026 07:30:00 GMT",
                    link="http://example.com/current-story",
                )
            ),
            page_url: FakeResponse(article_html(page_url, "2026-07-19T07:30:00Z")),
        }
    )

    signals, status = fetch_feed(session, source, now, now - timedelta(hours=24), 10)

    assert len(signals) == 1
    assert signals[0].url == page_url
    assert page_url in session.requested_urls
    assert "http://example.com/current-story" not in session.requested_urls
    assert status["ok"] is True


def test_feed_candidates_prefer_current_verified_data_over_older_candidate():
    source = BusinessSource(
        source_id="sample",
        name="Sample First Party",
        homepage_url="https://example.com/news",
        feed_url="https://example.com/old.xml",
        lane="ai_commercialization",
        authority_tier="tier_1",
        feed_candidates=("https://example.com/old.xml", "https://example.com/current.xml"),
        entry_hosts=("example.com",),
    )
    now = datetime(2026, 7, 19, 8, 0, tzinfo=timezone.utc)
    session = FakeSession(
        {
            "https://example.com/old.xml": FakeResponse(
                rss_item(published="Fri, 17 Jul 2026 07:30:00 GMT")
            ),
            "https://example.com/current.xml": FakeResponse(
                rss_item(published="Sun, 19 Jul 2026 07:30:00 GMT")
            ),
        }
    )

    signals, status = fetch_feed(session, source, now, now - timedelta(hours=24), 10)

    assert len(signals) == 1
    assert status["selected_url"] == "https://example.com/current.xml"
    assert status["attempted_urls"] == list(source.feed_candidates)


def test_jsonld_does_not_combine_unrelated_article_title_and_timestamp():
    page_url = "https://example.com/current-story"
    payload = {
        "@graph": [
            {
                "@type": "NewsArticle",
                "headline": "Current story without a timestamp",
                "url": page_url,
            },
            {
                "@type": "NewsArticle",
                "headline": "Related fresh story",
                "url": "https://example.com/related-story",
                "datePublished": "2026-07-19T07:59:00Z",
            },
        ]
    }
    html = f"""
    <html><head><link rel="canonical" href="{page_url}"></head>
    <body><script type="application/ld+json">{json.dumps(payload)}</script></body></html>
    """

    with pytest.raises(ValueError, match="title_or_timestamp_missing"):
        publication_page_metadata(html.encode(), page_url, {"example.com"})


def test_generic_meta_date_is_not_treated_as_article_publication_time():
    page_url = "https://example.com/current-story"
    html = f"""
    <html><head>
      <link rel="canonical" href="{page_url}">
      <meta property="og:title" content="Current story">
      <meta name="date" content="2026-07-19T07:59:00Z">
    </head></html>
    """

    with pytest.raises(ValueError, match="title_or_timestamp_missing"):
        publication_page_metadata(html.encode(), page_url, {"example.com"})


def test_conflicting_page_bound_publication_times_fail_closed():
    page_url = "https://example.com/current-story"
    payload = {
        "@type": "NewsArticle",
        "headline": "Current story",
        "url": page_url,
        "datePublished": "2026-07-18T07:59:00Z",
    }
    html = f"""
    <html><head>
      <link rel="canonical" href="{page_url}">
      <meta property="og:title" content="Current story">
      <meta property="article:published_time" content="2026-07-19T07:59:00Z">
      <script type="application/ld+json">{json.dumps(payload)}</script>
    </head></html>
    """

    with pytest.raises(ValueError, match="timestamp_conflict"):
        publication_page_metadata(html.encode(), page_url, {"example.com"})


def test_safe_get_rejects_redirect_to_unreviewed_host():
    start_url = "https://example.com/feed.xml"
    session = FakeSession(
        {
            start_url: FakeResponse("", 302, {"Location": "https://outside.example/internal"}),
        }
    )

    with pytest.raises(ValueError, match="host_not_allowed"):
        safe_get(
            session,
            start_url,
            allowed_hosts={"example.com"},
            budget=FetchBudget(),
            timeout=6,
            max_bytes=1024,
        )


def test_safe_get_follows_only_explicit_reviewed_https_redirect():
    start_url = "https://example.com/feed.xml"
    cdn_url = "https://cdn.example.net/feed.xml"
    session = FakeSession(
        {
            start_url: FakeResponse("", 302, {"Location": cdn_url}),
            cdn_url: FakeResponse("verified"),
        }
    )

    response = safe_get(
        session,
        start_url,
        allowed_hosts={"example.com", "cdn.example.net"},
        budget=FetchBudget(),
        timeout=6,
        max_bytes=1024,
    )

    assert response.url == cdn_url
    assert response.content == b"verified"
    assert session.requested_urls == [start_url, cdn_url]


def test_safe_get_rejects_non_public_resolution_on_every_hop(monkeypatch):
    start_url = "https://example.com/feed.xml"

    def reject_private(hostname: str):
        raise ValueError(f"non_public_destination_rejected: {hostname}")

    monkeypatch.setattr(business_evidence, "resolve_public_addresses", reject_private)
    session = FakeSession({start_url: FakeResponse("ok")})

    with pytest.raises(ValueError, match="non_public_destination_rejected"):
        safe_get(
            session,
            start_url,
            allowed_hosts={"example.com"},
            budget=FetchBudget(),
            timeout=6,
            max_bytes=1024,
        )


def test_safe_get_pins_the_validated_address_for_real_sessions(monkeypatch):
    start_url = "https://example.com/feed.xml"
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        business_evidence,
        "resolve_public_addresses",
        lambda hostname: ("93.184.216.34",),
    )

    def fake_pinned_get(session, url, addresses, timeout, budget):
        captured.update({"url": url, "addresses": addresses})
        budget.begin_request()
        return FakeResponse("verified")

    monkeypatch.setattr(business_evidence, "_pinned_session_get", fake_pinned_get)

    response = safe_get(
        requests.Session(),
        start_url,
        allowed_hosts={"example.com"},
        budget=FetchBudget(),
        timeout=6,
        max_bytes=1024,
    )

    assert response.content == b"verified"
    assert captured == {"url": start_url, "addresses": ("93.184.216.34",)}


def test_source_deadline_fails_closed_before_network_access():
    start_url = "https://example.com/feed.xml"
    session = FakeSession({start_url: FakeResponse("verified")})
    budget = FetchBudget(deadline_monotonic=time.monotonic() - 1)

    with pytest.raises(ValueError, match="source_deadline_exceeded"):
        safe_get(
            session,
            start_url,
            allowed_hosts={"example.com"},
            budget=budget,
            timeout=6,
            max_bytes=1024,
        )
    assert session.requested_urls == []


def test_response_and_total_byte_budgets_fail_closed():
    first_url = "https://example.com/first"
    second_url = "https://example.com/second"
    session = FakeSession(
        {
            first_url: FakeResponse(b"1234"),
            second_url: FakeResponse(b"5678"),
        }
    )
    budget = FetchBudget(remaining_requests=2, remaining_bytes=6)

    first = safe_get(
        session,
        first_url,
        allowed_hosts={"example.com"},
        budget=budget,
        timeout=6,
        max_bytes=4,
    )
    assert first.content == b"1234"
    with pytest.raises(ValueError, match="response_body_too_large"):
        safe_get(
            session,
            second_url,
            allowed_hosts={"example.com"},
            budget=budget,
            timeout=6,
            max_bytes=4,
        )
    assert session.responses[second_url].closed is True


def test_gzip_sitemap_decompression_budget_fails_closed(monkeypatch):
    source = next(item for item in SOURCES if item.source_id == "tinyseed")
    monkeypatch.setattr(business_evidence, "MAX_SITEMAP_DECOMPRESSED_BYTES", 64)
    compressed = gzip.compress(b"<urlset>" + b"x" * 128 + b"</urlset>")

    with pytest.raises(ValueError, match="decompressed_too_large"):
        sitemap_candidates(compressed, source)


def test_sitemap_url_budget_counts_unreviewed_nodes_before_filtering(monkeypatch):
    source = next(item for item in SOURCES if item.source_id == "tinyseed")
    monkeypatch.setattr(business_evidence, "MAX_SITEMAP_URLS", 2)
    body = """
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://outside.example/1</loc><lastmod>2026-07-19</lastmod></url>
      <url><loc>https://outside.example/2</loc><lastmod>2026-07-19</lastmod></url>
      <url><loc>https://outside.example/3</loc><lastmod>2026-07-19</lastmod></url>
    </urlset>
    """

    with pytest.raises(ValueError, match="url_budget_exceeded"):
        sitemap_candidates(body.encode(), source)


def test_recurring_source_paths_accept_2027_without_code_changes():
    bcg = next(item for item in SOURCES if item.source_id == "bcg_ai")
    tinyseed = next(item for item in SOURCES if item.source_id == "tinyseed")

    assert re.search(bcg.entry_path_pattern, "/publications/2027/ai-agent-economics")
    assert re.search(tinyseed.entry_path_pattern, "/spring-2027/founder-batch")
