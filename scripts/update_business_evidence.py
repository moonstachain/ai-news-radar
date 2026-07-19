#!/usr/bin/env python3
"""Build the English-only business evidence layer for Yuanli IP radar.

The layer is deliberately separate from the 24h AI news feed. It reads only
public English sources and publishes compact evidence artifacts for AI business
models, one-person companies, founder cases, and counter-signals.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import ipaddress
import json
import math
import re
import socket
import ssl
import time
import warnings
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

import requests
import urllib3
from bs4 import BeautifulSoup
from bs4 import MarkupResemblesLocatorWarning

try:
    import feedparser
except Exception:  # pragma: no cover - optional dependency branch
    feedparser = None


UA = "AI-News-Radar-Business-Evidence/1.0 (+https://github.com/moonstachain/ai-news-radar)"
TIMEOUT = 6
DEFAULT_WINDOW_HOURS = 24
MAX_REDIRECTS = 5
MAX_FEED_BYTES = 8 * 1024 * 1024
MAX_PAGE_BYTES = 4 * 1024 * 1024
MAX_SITEMAP_BYTES = 24 * 1024 * 1024
MAX_SITEMAP_DECOMPRESSED_BYTES = 48 * 1024 * 1024
MAX_SOURCE_BYTES = 96 * 1024 * 1024
MAX_SOURCE_REQUESTS = 40
MAX_SOURCE_SECONDS = 75
MAX_SITEMAP_URLS = 50_000
TIMESTAMP_SKIP_REASONS = (
    "missing_timestamp",
    "invalid_timestamp",
    "future_timestamp",
    "outside_window",
    "conflicted_timestamp",
    "unverified_page",
)
warnings.filterwarnings("ignore", category=MarkupResemblesLocatorWarning)

LANE_LABELS = {
    "authority": "Authority",
    "startup_vc": "Startup / VC",
    "opc": "OPC",
    "ai_commercialization": "AI Commercialization",
}

AUTHORITY_SCORE = {
    "tier_1": 20,
    "tier_2": 16,
    "tier_3": 12,
}

BUSINESS_KEYWORDS = {
    "AI Business Model Innovation": [
        "business model",
        "monetization",
        "pricing",
        "revenue",
        "go-to-market",
        "gtm",
        "marketplace",
        "vertical ai",
        "agentic",
        "ai agent",
        "workflow",
        "automation",
        "platform",
        "services",
        "outcome",
    ],
    "OPC": [
        "one-person",
        "one person",
        "solo founder",
        "indie hacker",
        "bootstrapped",
        "tiny team",
        "small team",
        "micro-saas",
        "micro saas",
        "creator",
    ],
    "Founder Case": [
        "founder",
        "startup",
        "case study",
        "interview",
        "built",
        "launched",
        "growth",
        "scaled",
        "customer",
    ],
    "Enterprise AI Workflow": [
        "enterprise",
        "workflow",
        "operations",
        "productivity",
        "copilot",
        "agent",
        "adoption",
        "implementation",
        "organization",
    ],
    "Counter Signal": [
        "risk",
        "failed",
        "failure",
        "challenge",
        "concern",
        "lawsuit",
        "regulation",
        "layoff",
        "margin",
        "roi",
        "not ready",
        "bubble",
    ],
}

YUANLI_KEYWORDS = {
    "yuanli_asset": ["asset", "moat", "distribution", "audience", "brand", "data", "workflow"],
    "yuanli_startup": ["startup", "founder", "gtm", "pricing", "customer", "revenue", "sales"],
    "yuanli_os": ["workflow", "agent", "automation", "copilot", "system", "operating model"],
    "ftf_trust": ["case study", "evidence", "survey", "report", "data", "research", "benchmark"],
    "profit_container": ["pricing", "margin", "revenue", "subscription", "service", "marketplace"],
    "wealth_flywheel": ["flywheel", "growth", "distribution", "retention", "compounding", "network"],
}


@dataclass(frozen=True)
class BusinessSource:
    source_id: str
    name: str
    homepage_url: str
    feed_url: str
    lane: str
    authority_tier: str
    access_method: str = "public"
    capture_method: str = "rss_or_public_page"
    cadence: str = "30m"
    health_status: str = "unknown"
    last_checked_at: str = ""
    capture_mode: str = "feed"
    feed_candidates: tuple[str, ...] = ()
    sitemap_urls: tuple[str, ...] = ()
    entry_hosts: tuple[str, ...] = ()
    transport_hosts: tuple[str, ...] = ()
    entry_path_pattern: str = ""
    entry_base_url: str = ""
    require_entry_page_cross_check: bool = False
    candidate_limit: int = 12
    freshness_sla_hours: int = 336
    next_review_at: str = ""


@dataclass
class BusinessSignal:
    signal_id: str
    title: str
    url: str
    source_id: str
    source_name: str
    published_at: str
    captured_at: str
    lane: str
    entities: list[str]
    business_model_tags: list[str]
    yuanli_tags: list[str]
    opc_fit_score: int
    case_concreteness_score: int
    total_score: int
    score_breakdown: dict[str, int]
    summary: str
    timestamp_basis: str = ""
    transport_mode: str = ""


@dataclass
class FetchBudget:
    remaining_requests: int = MAX_SOURCE_REQUESTS
    remaining_bytes: int = MAX_SOURCE_BYTES
    deadline_monotonic: float = field(default_factory=lambda: time.monotonic() + MAX_SOURCE_SECONDS)

    def remaining_seconds(self) -> float:
        remaining = self.deadline_monotonic - time.monotonic()
        if remaining <= 0:
            raise ValueError("source_deadline_exceeded")
        return remaining

    def begin_request(self) -> None:
        self.remaining_seconds()
        if self.remaining_requests <= 0:
            raise ValueError("source_request_budget_exceeded")
        self.remaining_requests -= 1

    def consume_bytes(self, size: int) -> None:
        self.remaining_seconds()
        if size < 0 or size > self.remaining_bytes:
            raise ValueError("source_byte_budget_exceeded")
        self.remaining_bytes -= size


@dataclass(frozen=True)
class SafeResponse:
    url: str
    status_code: int
    headers: dict[str, str]
    content: bytes

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code} for {self.url}")


class PinnedHTTPResponse:
    def __init__(self, response: urllib3.response.HTTPResponse, pool: Any):
        self._response = response
        self._pool = pool
        self.status_code = int(response.status)
        self.headers = dict(response.headers)

    def iter_content(self, chunk_size: int):
        yield from self._response.stream(amt=chunk_size, decode_content=False)

    def close(self) -> None:
        try:
            self._response.close()
        finally:
            self._pool.close()


SOURCES: list[BusinessSource] = [
    BusinessSource("mckinsey_ai", "McKinsey / QuantumBlack", "https://www.mckinsey.com/capabilities/quantumblack/our-insights", "https://www.mckinsey.com/featured-insights/rss", "authority", "tier_1", capture_mode="manual", next_review_at="2026-07-26T00:00:00Z"),
    BusinessSource("bcg_ai", "BCG AI Insights", "https://www.bcg.com/capabilities/artificial-intelligence/insights", "https://www.bcg.com/rss", "authority", "tier_1", capture_mode="sitemap", sitemap_urls=("https://www.bcg.com/google_sitemap-content.xml",), entry_hosts=("bcg.com", "www.bcg.com"), entry_path_pattern=r"^/publications/20\d{2}/.*(?:ai|agent|genai|artificial-intelligence)", freshness_sla_hours=720),
    BusinessSource("bain_insights", "Bain Insights", "https://www.bain.com/insights/", "https://www.bain.com/insights/rss/", "authority", "tier_1", feed_candidates=("https://www.bain.com/rss-feed/", "https://www.bain.com/insights/rss/"), entry_hosts=("bain.com", "www.bain.com"), freshness_sla_hours=720),
    BusinessSource("hbr", "Harvard Business Review", "https://hbr.org/", "https://feeds.hbr.org/harvardbusiness", "authority", "tier_1", feed_candidates=("http://feeds.hbr.org/harvardbusiness", "https://feeds.hbr.org/harvardbusiness"), entry_hosts=("hbr.org", "www.hbr.org"), entry_base_url="https://hbr.org/", require_entry_page_cross_check=True, freshness_sla_hours=72),
    BusinessSource("mit_smr", "MIT Sloan Management Review", "https://sloanreview.mit.edu/", "https://sloanreview.mit.edu/feed/", "authority", "tier_1"),
    BusinessSource("knowledge_wharton", "Knowledge at Wharton", "https://knowledge.wharton.upenn.edu/", "https://knowledge.wharton.upenn.edu/feed/", "authority", "tier_2"),
    BusinessSource("yc_blog", "Y Combinator Blog", "https://www.ycombinator.com/blog", "https://www.ycombinator.com/blog/rss", "startup_vc", "tier_1"),
    BusinessSource("a16z", "a16z", "https://a16z.com/ai/", "https://a16z.com/feed/", "startup_vc", "tier_1", capture_mode="sitemap", sitemap_urls=("https://a16z.com/post-sitemap3.xml", "https://a16z.com/announcement-sitemap.xml"), entry_hosts=("a16z.com", "www.a16z.com")),
    BusinessSource("first_round", "First Round Review", "https://review.firstround.com/", "https://review.firstround.com/rss/", "startup_vc", "tier_1", capture_mode="sitemap", sitemap_urls=("https://review.firstround.com/sitemap-posts.xml",), entry_hosts=("review.firstround.com",), freshness_sla_hours=720),
    BusinessSource("lenny", "Lenny's Newsletter", "https://www.lennysnewsletter.com/", "https://www.lennysnewsletter.com/feed", "startup_vc", "tier_2", freshness_sla_hours=240),
    BusinessSource("generalist", "The Generalist", "https://www.generalist.com/", "https://www.generalist.com/feed", "startup_vc", "tier_2", freshness_sla_hours=240),
    BusinessSource("not_boring", "Not Boring", "https://www.notboring.co/", "https://www.notboring.co/feed", "startup_vc", "tier_2", freshness_sla_hours=240),
    BusinessSource("cbinsights", "CB Insights", "https://www.cbinsights.com/research/", "https://www.cbinsights.com/research/feed/", "startup_vc", "tier_2"),
    BusinessSource("indie_hackers", "Indie Hackers", "https://www.indiehackers.com/", "https://www.indiehackers.com/feed.xml", "opc", "tier_2", capture_mode="page_detail", entry_hosts=("indiehackers.com", "www.indiehackers.com"), entry_path_pattern=r"^/post/", freshness_sla_hours=72),
    BusinessSource("starter_story", "Starter Story", "https://www.starterstory.com/", "https://www.starterstory.com/feed", "opc", "tier_2", capture_mode="sitemap", sitemap_urls=("https://www.starterstory.com/sitemap",), entry_hosts=("starterstory.com", "www.starterstory.com"), transport_hosts=("d1coqmn8qm80r4.cloudfront.net",), entry_path_pattern=r"^/stories/"),
    BusinessSource("microconf", "MicroConf", "https://microconf.com/", "https://microconf.com/feed", "opc", "tier_2", feed_candidates=("https://microconf.com/latest?format=rss", "https://microconf.com/feed"), entry_hosts=("microconf.com", "www.microconf.com")),
    BusinessSource("tinyseed", "TinySeed", "https://tinyseed.com/", "https://tinyseed.com/feed", "opc", "tier_2", capture_mode="sitemap", sitemap_urls=("https://tinyseed.com/sitemap.xml",), entry_hosts=("tinyseed.com", "www.tinyseed.com"), entry_path_pattern=r"^/(?:spring|summer|fall|autumn|winter)-20\d{2}/", freshness_sla_hours=720),
    BusinessSource("bootstrapped_founder", "The Bootstrapped Founder", "https://thebootstrappedfounder.com/", "https://thebootstrappedfounder.com/feed.xml", "opc", "tier_2", feed_candidates=("https://thebootstrappedfounder.com/feed/", "https://thebootstrappedfounder.com/feed.xml"), entry_hosts=("thebootstrappedfounder.com", "www.thebootstrappedfounder.com")),
    BusinessSource("levelsio", "levels.io", "https://levels.io/", "https://levels.io/rss/", "opc", "tier_2", freshness_sla_hours=720),
    BusinessSource("latent_space", "Latent Space", "https://www.latent.space/", "https://www.latent.space/feed", "ai_commercialization", "tier_2"),
    BusinessSource("ai_engineer", "AI Engineer", "https://www.ai.engineer/", "https://www.ai.engineer/feed", "ai_commercialization", "tier_2", capture_mode="manual", next_review_at="2026-07-26T00:00:00Z"),
    BusinessSource("the_batch", "The Batch", "https://www.deeplearning.ai/the-batch/", "https://www.deeplearning.ai/the-batch/rss/", "ai_commercialization", "tier_2", feed_candidates=("https://charonhub.deeplearning.ai/rss/", "https://www.deeplearning.ai/the-batch/rss/"), entry_hosts=("charonhub.deeplearning.ai",)),
    BusinessSource("openai_news", "OpenAI News", "https://openai.com/news/", "https://openai.com/news/rss.xml", "ai_commercialization", "tier_1"),
    BusinessSource("anthropic_news", "Anthropic News", "https://www.anthropic.com/news", "https://www.anthropic.com/news/rss.xml", "ai_commercialization", "tier_1", capture_mode="sitemap", sitemap_urls=("https://www.anthropic.com/sitemap.xml",), entry_hosts=("anthropic.com", "www.anthropic.com"), entry_path_pattern=r"^/news/"),
    BusinessSource("github_blog", "GitHub Blog", "https://github.blog/", "https://github.blog/feed/", "ai_commercialization", "tier_1", freshness_sla_hours=168),
    BusinessSource("huggingface_blog", "Hugging Face Blog", "https://huggingface.co/blog", "https://huggingface.co/blog/feed.xml", "ai_commercialization", "tier_2", freshness_sla_hours=168),
]


def now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z")


def parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    text = str(value).strip()
    try:
        parsed = parsedate_to_datetime(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        pass
    try:
        normalized = text.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def empty_timestamp_skips() -> dict[str, int]:
    return {reason: 0 for reason in TIMESTAMP_SKIP_REASONS}


def validate_published_time(
    value: Any,
    now: datetime,
    window_start: datetime,
    skips: dict[str, int],
) -> datetime | None:
    if value is None or not str(value).strip():
        skips["missing_timestamp"] += 1
        return None
    published = parse_time(value)
    if published is None:
        skips["invalid_timestamp"] += 1
        return None
    if published > now:
        skips["future_timestamp"] += 1
        return None
    if published < window_start:
        skips["outside_window"] += 1
        return None
    return published


def stable_id(*parts: str, prefix: str = "biz") -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


def clean_text(text: Any) -> str:
    if text is None:
        return ""
    raw = str(text)
    plain = BeautifulSoup(raw, "html.parser").get_text(" ") if "<" in raw or "&" in raw else raw
    collapsed = re.sub(r"\s+", " ", plain)
    return collapsed.strip()


def host(url: str) -> str:
    return urlparse(url).netloc.replace("www.", "")


def resolve_public_addresses(hostname: str) -> tuple[str, ...]:
    """Resolve a reviewed hostname and reject any non-public destination."""
    try:
        literal = ipaddress.ip_address(hostname)
        addresses = {literal}
    except ValueError:
        try:
            addresses = {
                ipaddress.ip_address(row[4][0])
                for row in socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
            }
        except (OSError, ValueError) as exc:
            raise ValueError(f"public_host_resolution_failed: {hostname}") from exc
    if not addresses or any(not address.is_global for address in addresses):
        raise ValueError(f"non_public_destination_rejected: {hostname}")
    return tuple(str(address) for address in sorted(addresses, key=lambda value: (value.version, str(value))))


def validate_remote_url(url: str, allowed_hosts: set[str], *, allow_http: bool = False) -> tuple[str, ...]:
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    if parsed.username or parsed.password:
        raise ValueError("remote_url_credentials_rejected")
    if parsed.scheme not in ({"https", "http"} if allow_http else {"https"}):
        raise ValueError(f"remote_url_scheme_rejected: {parsed.scheme or 'missing'}")
    if hostname not in {value.lower() for value in allowed_hosts}:
        raise ValueError(f"remote_url_host_not_allowed: {hostname}")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("remote_url_port_invalid") from exc
    expected_port = 80 if parsed.scheme == "http" else 443
    if port not in (None, expected_port):
        raise ValueError(f"remote_url_port_rejected: {port}")
    return resolve_public_addresses(hostname)


def _pinned_session_get(
    session: requests.Session,
    url: str,
    addresses: tuple[str, ...],
    timeout: int,
    budget: FetchBudget,
) -> PinnedHTTPResponse:
    """Connect to a validated IP while preserving the reviewed Host and TLS SNI."""
    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    request_target = urlunparse(("", "", parsed.path or "/", parsed.params, parsed.query, ""))
    headers = {str(key): str(value) for key, value in session.headers.items()}
    headers["Host"] = hostname
    headers["Accept-Encoding"] = "identity"
    errors: list[str] = []
    for address in addresses:
        budget.begin_request()
        request_timeout = max(1.0, min(float(timeout), budget.remaining_seconds()))
        if parsed.scheme == "https":
            pool: Any = urllib3.HTTPSConnectionPool(
                address,
                port=443,
                maxsize=1,
                block=True,
                assert_hostname=hostname,
                server_hostname=hostname,
                ssl_context=ssl.create_default_context(),
            )
        else:
            pool = urllib3.HTTPConnectionPool(address, port=80, maxsize=1, block=True)
        try:
            response = pool.urlopen(
                "GET",
                request_target,
                headers=headers,
                redirect=False,
                retries=False,
                preload_content=False,
                decode_content=False,
                timeout=urllib3.Timeout(connect=request_timeout, read=request_timeout, total=request_timeout),
            )
            return PinnedHTTPResponse(response, pool)
        except Exception as exc:
            pool.close()
            errors.append(f"{address}: {str(exc)[:160]}")
    raise ValueError("pinned_public_connection_failed: " + " | ".join(errors[:3]))


def _response_headers(response: Any) -> dict[str, str]:
    return {str(key).lower(): str(value) for key, value in dict(getattr(response, "headers", {}) or {}).items()}


def _read_response_body(response: Any, max_bytes: int, budget: FetchBudget) -> bytes:
    headers = _response_headers(response)
    content_length = headers.get("content-length")
    if content_length:
        try:
            declared_size = int(content_length)
        except ValueError as exc:
            raise ValueError("response_content_length_invalid") from exc
        if declared_size < 0 or declared_size > max_bytes or declared_size > budget.remaining_bytes:
            raise ValueError("response_body_too_large")

    chunks: list[bytes] = []
    total = 0
    iterator = getattr(response, "iter_content", None)
    if callable(iterator):
        stream = iterator(chunk_size=64 * 1024)
    else:
        stream = (bytes(getattr(response, "content", b"")),)
    for chunk in stream:
        budget.remaining_seconds()
        if not chunk:
            continue
        raw = bytes(chunk)
        total += len(raw)
        if total > max_bytes or total > budget.remaining_bytes:
            raise ValueError("response_body_too_large")
        chunks.append(raw)
    budget.consume_bytes(total)
    return b"".join(chunks)


def safe_get(
    session: requests.Session,
    url: str,
    *,
    allowed_hosts: set[str],
    budget: FetchBudget,
    timeout: int,
    max_bytes: int,
    allow_http_initial: bool = False,
) -> SafeResponse:
    """Fetch with per-hop scheme, host, IP, redirect and byte-budget checks."""
    current_url = url
    for hop in range(MAX_REDIRECTS + 1):
        addresses = validate_remote_url(current_url, allowed_hosts, allow_http=allow_http_initial and hop == 0)
        if isinstance(session, requests.Session):
            response = _pinned_session_get(session, current_url, addresses, timeout, budget)
        else:
            budget.begin_request()
            request_timeout = max(1, min(timeout, math.ceil(budget.remaining_seconds())))
            try:
                response = session.get(current_url, timeout=request_timeout, allow_redirects=False, stream=True)
            except TypeError:
                # Minimal test doubles may implement only the historical get(url, timeout) API.
                response = session.get(current_url, timeout=request_timeout)
        status_code = int(getattr(response, "status_code", 0) or 0)
        headers = _response_headers(response)
        if status_code in {301, 302, 303, 307, 308}:
            location = headers.get("location", "").strip()
            close = getattr(response, "close", None)
            if callable(close):
                close()
            if not location:
                raise ValueError("redirect_location_missing")
            if hop >= MAX_REDIRECTS:
                raise ValueError("redirect_limit_exceeded")
            current_url = urljoin(current_url, location)
            continue
        close = getattr(response, "close", None)
        try:
            body = _read_response_body(response, max_bytes, budget)
        finally:
            if callable(close):
                close()
        result = SafeResponse(current_url, status_code, headers, body)
        result.raise_for_status()
        return result
    raise ValueError("redirect_limit_exceeded")


def allowed_entry_hosts(source: BusinessSource) -> set[str]:
    configured = {value.lower() for value in source.entry_hosts if value}
    homepage_host = (urlparse(source.homepage_url).hostname or "").lower()
    if homepage_host:
        configured.add(homepage_host)
        configured.add(homepage_host.removeprefix("www."))
        configured.add(f"www.{homepage_host.removeprefix('www.')}")
    return configured


def allowed_transport_hosts(source: BusinessSource) -> set[str]:
    values = {
        source.homepage_url,
        source.feed_url,
        *source.feed_candidates,
        *source.sitemap_urls,
    }
    configured = allowed_entry_hosts(source)
    configured.update(value.lower() for value in source.transport_hosts if value)
    configured.update((urlparse(value).hostname or "").lower() for value in values if value)
    configured.discard("")
    return configured


def normalized_public_url(value: str) -> str:
    parsed = urlparse(value)
    path = parsed.path.rstrip("/") or "/"
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{path}"


def canonical_entry_url(source: BusinessSource, feed_url: str, raw_url: str) -> str:
    value = urljoin(source.entry_base_url or feed_url, raw_url)
    parsed = urlparse(value)
    if (
        source.require_entry_page_cross_check
        and parsed.scheme == "http"
        and (parsed.hostname or "").lower() in allowed_entry_hosts(source)
    ):
        value = urlunparse(parsed._replace(scheme="https", netloc=parsed.hostname or ""))
    return value


def walk_json_objects(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk_json_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_json_objects(child)


def _jsonld_types(item: dict[str, Any]) -> set[str]:
    raw = item.get("@type")
    values = raw if isinstance(raw, list) else [raw]
    return {str(value).rsplit("/", 1)[-1].lower() for value in values if value}


def _jsonld_urls(item: dict[str, Any], requested_url: str) -> list[str]:
    values: list[Any] = [item.get("url"), item.get("@id")]
    main_entity = item.get("mainEntityOfPage")
    if isinstance(main_entity, dict):
        values.extend((main_entity.get("url"), main_entity.get("@id")))
    else:
        values.append(main_entity)
    return [urljoin(requested_url, value.strip()) for value in values if isinstance(value, str) and value.strip()]


def jsonld_article_metadata(payload: Any, requested_url: str, allowed_hosts: set[str]) -> dict[str, str]:
    """Accept title, date and URL only from one matching article object."""
    matches: list[dict[str, str]] = []
    for item in walk_json_objects(payload):
        if not {"article", "newsarticle", "blogposting"}.intersection(_jsonld_types(item)):
            continue
        title = clean_text(item.get("headline"))
        published = str(item.get("datePublished") or "").strip()
        if not title or not published:
            continue
        for candidate_url in _jsonld_urls(item, requested_url):
            hostname = (urlparse(candidate_url).hostname or "").lower()
            if hostname not in allowed_hosts:
                continue
            if normalized_public_url(candidate_url) == normalized_public_url(requested_url):
                matches.append({"title": title, "published": published, "url": candidate_url})
                break
    parsed_times = {parse_time(row["published"]) for row in matches}
    if None in parsed_times:
        raise ValueError("publication_page_jsonld_timestamp_invalid")
    if len(parsed_times) > 1:
        raise ValueError("publication_page_timestamp_conflict")
    return matches[0] if matches else {}


def next_frame_post_metadata(soup: BeautifulSoup, requested_url: str) -> dict[str, str]:
    """Read a page-bound post object from Next.js RSC frames when present."""
    requested_slug = urlparse(requested_url).path.rstrip("/").rsplit("/", 1)[-1]
    for script in soup.find_all("script"):
        raw = script.string or script.get_text()
        marker = "self.__next_f.push("
        if marker not in raw:
            continue
        expression = raw.split(marker, 1)[1].rsplit(")", 1)[0]
        try:
            frame = json.loads(expression)[1]
        except (IndexError, TypeError, ValueError):
            continue
        if not isinstance(frame, str):
            continue
        search_from = 0
        while True:
            marker_match = re.search(r'"post"\s*:\s*{', frame[search_from:])
            if marker_match is None:
                break
            object_start = search_from + marker_match.end() - 1
            try:
                post, consumed = json.JSONDecoder().raw_decode(frame[object_start:])
            except (TypeError, ValueError):
                search_from = object_start + 1
                continue
            search_from = object_start + consumed
            if not isinstance(post, dict):
                continue
            slug = post.get("slug")
            slug_value = slug.get("current") if isinstance(slug, dict) else slug
            if slug_value != requested_slug:
                continue
            title = clean_text(post.get("title"))
            published = str(post.get("publishedOn") or post.get("publishedAt") or "").strip()
            if title and published:
                return {"title": title, "published": published}
    return {}


def publication_page_metadata(body: bytes, requested_url: str, allowed_hosts: set[str]) -> dict[str, Any]:
    """Extract title, canonical URL and a page-bound provider publication time."""
    if len(body) > MAX_PAGE_BYTES:
        raise ValueError("publication_page_too_large")
    soup = BeautifulSoup(body, "html.parser")
    metadata: dict[str, str] = {}
    for node in soup.find_all("meta"):
        key = str(node.get("property") or node.get("name") or node.get("itemprop") or "").lower()
        value = str(node.get("content") or "").strip()
        if key and value and key not in metadata:
            metadata[key] = value

    canonical_node = soup.find("link", rel=lambda value: value and "canonical" in str(value).lower())
    canonical = urljoin(requested_url, str(canonical_node.get("href") or "")) if canonical_node else requested_url
    has_explicit_canonical = canonical_node is not None
    meta_title = metadata.get("og:title") or metadata.get("twitter:title")
    meta_published = metadata.get("article:published_time") or metadata.get("datepublished")

    matching_articles: list[dict[str, str]] = []
    for node in soup.find_all("script", type="application/ld+json"):
        try:
            payload = json.loads(node.string or node.get_text())
        except (TypeError, ValueError):
            continue
        matching_article = jsonld_article_metadata(payload, requested_url, allowed_hosts)
        if matching_article:
            matching_articles.append(matching_article)

    frame = next_frame_post_metadata(soup, requested_url)
    timestamp_candidates = {
        "meta": meta_published,
        **{f"jsonld_{index}": row["published"] for index, row in enumerate(matching_articles)},
        "next_frame": frame.get("published"),
    }
    parsed_timestamps: dict[str, datetime] = {}
    for label, value in timestamp_candidates.items():
        if not str(value or "").strip():
            continue
        parsed_value = parse_time(value)
        if parsed_value is None:
            raise ValueError(f"publication_page_timestamp_invalid: {label}")
        parsed_timestamps[label] = parsed_value
    if len(set(parsed_timestamps.values())) > 1:
        raise ValueError("publication_page_timestamp_conflict")

    title = meta_title or (matching_articles[0]["title"] if matching_articles else "") or frame.get("title")
    published = next(iter(parsed_timestamps.values()), None)
    if matching_articles and not has_explicit_canonical:
        canonical = matching_articles[0]["url"]

    title = clean_text(title or (soup.title.get_text(" ") if soup.title else ""))
    canonical_host = (urlparse(canonical).hostname or "").lower()
    if canonical_host not in allowed_hosts:
        raise ValueError(f"publication_page_canonical_host_not_allowed: {canonical_host}")
    if normalized_public_url(canonical) != normalized_public_url(requested_url):
        raise ValueError("publication_page_canonical_url_mismatch")
    if not title or published is None:
        raise ValueError("publication_page_title_or_timestamp_missing")
    return {"title": title, "url": canonical, "published": published}


def sitemap_candidates(
    body: bytes,
    source: BusinessSource,
    budget: FetchBudget | None = None,
) -> list[dict[str, Any]]:
    if len(body) > MAX_SITEMAP_BYTES:
        raise ValueError("official_sitemap_too_large")
    if body[:2] == b"\x1f\x8b":
        with gzip.GzipFile(fileobj=io.BytesIO(body)) as compressed:
            decompressed = compressed.read(MAX_SITEMAP_DECOMPRESSED_BYTES + 1)
        if len(decompressed) > MAX_SITEMAP_DECOMPRESSED_BYTES:
            raise ValueError("official_sitemap_decompressed_too_large")
        if budget is not None:
            budget.consume_bytes(max(0, len(decompressed) - len(body)))
        body = decompressed
    if len(body) > MAX_SITEMAP_DECOMPRESSED_BYTES:
        raise ValueError("official_sitemap_decompressed_too_large")
    upper_prefix = body[:4096].upper()
    if b"<!DOCTYPE" in upper_prefix or b"<!ENTITY" in upper_prefix:
        raise ValueError("official_sitemap_dtd_rejected")
    allowed_hosts = allowed_entry_hosts(source)
    rows: list[dict[str, Any]] = []
    total_url_nodes = 0
    root_name = ""
    try:
        for event, node in ET.iterparse(io.BytesIO(body), events=("start", "end")):
            local_name = node.tag.rsplit("}", 1)[-1]
            if not root_name and event == "start":
                root_name = local_name
            if event != "end" or local_name != "url":
                continue
            total_url_nodes += 1
            if total_url_nodes > MAX_SITEMAP_URLS:
                raise ValueError("official_sitemap_url_budget_exceeded")
            values = {
                child.tag.rsplit("}", 1)[-1]: clean_text("".join(child.itertext()))
                for child in list(node)
            }
            url = values.get("loc") or ""
            modified = parse_time(values.get("lastmod"))
            parsed = urlparse(url)
            if (
                url
                and modified is not None
                and (parsed.hostname or "").lower() in allowed_hosts
                and (
                    not source.entry_path_pattern
                    or re.search(source.entry_path_pattern, parsed.path, re.IGNORECASE)
                )
            ):
                rows.append({"url": url, "last_modified": modified})
            node.clear()
    except ET.ParseError as exc:
        raise ValueError("official_sitemap_invalid_xml") from exc
    if root_name != "urlset":
        raise ValueError("official_sitemap_unresolved_index")
    return sorted(rows, key=lambda row: row["last_modified"], reverse=True)


def keyword_score(text: str, keywords: list[str], weight: int) -> int:
    hay = text.lower()
    hits = sum(1 for kw in keywords if kw in hay)
    if hits <= 0:
        return 0
    return min(weight, math.ceil((hits / max(2, len(keywords) * 0.18)) * weight))


def match_tags(text: str, source: BusinessSource) -> list[str]:
    tags = [label for label, keywords in BUSINESS_KEYWORDS.items() if keyword_score(text, keywords, 10) > 0]
    if source.lane == "opc" and "OPC" not in tags:
        tags.append("OPC")
    if source.lane == "startup_vc" and "Founder Case" not in tags:
        tags.append("Founder Case")
    if source.lane == "authority" and "AI Business Model Innovation" not in tags:
        tags.append("AI Business Model Innovation")
    if source.lane == "ai_commercialization" and "Enterprise AI Workflow" not in tags:
        tags.append("Enterprise AI Workflow")
    return tags[:5]


def match_yuanli_tags(text: str) -> list[str]:
    tags = [label for label, keywords in YUANLI_KEYWORDS.items() if keyword_score(text, keywords, 10) > 0]
    return tags[:6] or ["yuanli_startup"]


def extract_entities(title: str, source: BusinessSource) -> list[str]:
    entities = [source.name]
    for match in re.findall(r"\b[A-Z][A-Za-z0-9&.\-]*(?:\s+[A-Z][A-Za-z0-9&.\-]*){0,2}\b", title):
        if len(match) > 2 and match not in entities and match.lower() not in {"ai", "the", "how", "why"}:
            entities.append(match)
        if len(entities) >= 6:
            break
    return entities


def build_summary(title: str, text: str, tags: list[str]) -> str:
    if text:
        summary = text[:220].strip()
        if len(text) > 220:
            summary += "..."
    else:
        summary = title
    return f"{summary} Tags: {', '.join(tags[:3])}."


def make_signal(
    source: BusinessSource,
    title: str,
    url: str,
    summary_text: str,
    published: datetime,
    now: datetime,
    *,
    timestamp_basis: str,
    transport_mode: str,
) -> BusinessSignal | None:
    if not title or not url:
        return None
    combined = f"{title} {summary_text}"
    tags = match_tags(combined, source)
    yuanli_tags = match_yuanli_tags(combined)
    total, breakdown, opc_fit, case_score = score_signal(source, title, summary_text, published, now)
    if total < 32 and len(tags) < 2:
        return None
    return BusinessSignal(
        signal_id=stable_id(source.source_id, url or title),
        title=title,
        url=url,
        source_id=source.source_id,
        source_name=source.name,
        published_at=published.isoformat().replace("+00:00", "Z"),
        captured_at=now.isoformat().replace("+00:00", "Z"),
        lane=source.lane,
        entities=extract_entities(title, source),
        business_model_tags=tags,
        yuanli_tags=yuanli_tags,
        opc_fit_score=opc_fit,
        case_concreteness_score=case_score,
        total_score=total,
        score_breakdown=breakdown,
        summary=build_summary(title, summary_text, tags),
        timestamp_basis=timestamp_basis,
        transport_mode=transport_mode,
    )


def score_signal(source: BusinessSource, title: str, summary: str, published_at: datetime, now: datetime) -> tuple[int, dict[str, int], int, int]:
    text = f"{title} {summary}".lower()
    source_authority = AUTHORITY_SCORE.get(source.authority_tier, 10)
    yuanli_relevance = min(20, sum(keyword_score(text, kws, 5) for kws in YUANLI_KEYWORDS.values()))
    business_model_value = min(18, sum(keyword_score(text, kws, 5) for label, kws in BUSINESS_KEYWORDS.items() if label != "OPC"))
    case_concreteness = min(15, keyword_score(text, BUSINESS_KEYWORDS["Founder Case"], 15) + (4 if re.search(r"\$|%|\d+\s*(m|k|million|billion|customers|users)", text) else 0))
    opc_fit = min(12, keyword_score(text, BUSINESS_KEYWORDS["OPC"], 12) + (5 if source.lane == "opc" else 0))
    age_hours = max(0.0, (now - published_at).total_seconds() / 3600)
    freshness = max(0, min(8, round(8 * math.exp(-age_hours / 168))))
    counter_signal = keyword_score(text, BUSINESS_KEYWORDS["Counter Signal"], 7)
    breakdown = {
        "source_authority": source_authority,
        "yuanli_relevance": yuanli_relevance,
        "business_model_value": business_model_value,
        "case_concreteness": case_concreteness,
        "opc_fit": opc_fit,
        "freshness": freshness,
        "counter_signal_value": counter_signal,
    }
    return min(100, sum(breakdown.values())), breakdown, opc_fit, case_concreteness


def local_structured_timestamp(anchor: Any, base_url: str = "") -> Any:
    """Return only a timestamp structurally attached to the linked story."""
    containers: list[Any] = []
    current = anchor.parent
    while current is not None and getattr(current, "name", None) not in {"body", "html"}:
        if getattr(current, "name", None) in {"article", "li", "section", "div"}:
            containers.append(current)
        current = current.parent
        if len(containers) >= 4:
            break

    for container in containers:
        base_host = (urlparse(base_url).hostname or "").lower()
        story_urls: set[str] = set()
        for node in container.find_all("a", href=True):
            raw_href = str(node.get("href") or "").strip()
            candidate_url = urljoin(base_url, raw_href)
            candidate = urlparse(candidate_url)
            if not raw_href or candidate.scheme != "https" or (candidate.hostname or "").lower() != base_host:
                continue
            story_urls.add(normalized_public_url(candidate_url))
        anchor_url = normalized_public_url(urljoin(base_url, str(anchor.get("href") or "")))
        if len(story_urls) != 1 or anchor_url not in story_urls:
            continue

        values: list[Any] = []
        values.extend(node.get("datetime") for node in container.find_all("time", datetime=True))
        for node in container.find_all(attrs={"itemprop": "datePublished"}):
            values.append(node.get("content") or node.get("datetime"))
        values.extend(
            node.get("content")
            for node in container.find_all("meta", attrs={"property": "article:published_time"})
        )
        timestamps = {str(value).strip() for value in values if str(value or "").strip()}
        if len(timestamps) == 1:
            return next(iter(timestamps))
    return None


def fetch_page_fallback(
    session: requests.Session,
    source: BusinessSource,
    now: datetime,
    window_start: datetime,
    max_per_source: int,
    budget: FetchBudget | None = None,
) -> tuple[list[BusinessSignal], dict[str, int]]:
    budget = budget or FetchBudget()
    allowed_hosts = allowed_entry_hosts(source)
    resp = safe_get(
        session,
        source.homepage_url,
        allowed_hosts=allowed_transport_hosts(source),
        budget=budget,
        timeout=TIMEOUT,
        max_bytes=MAX_PAGE_BYTES,
    )
    soup = BeautifulSoup(resp.text, "html.parser")
    signals: list[BusinessSignal] = []
    skips = empty_timestamp_skips()
    seen: set[str] = set()
    for anchor in soup.find_all("a"):
        title = clean_text(anchor.get_text(" "))
        href = str(anchor.get("href") or "").strip()
        if not title or len(title) < 18 or not href:
            continue
        href = urljoin(source.homepage_url, href)
        parsed_href = urlparse(href)
        if (
            parsed_href.scheme != "https"
            or (parsed_href.hostname or "").lower() not in allowed_hosts
            or href in seen
        ):
            continue
        if source.entry_path_pattern and not re.search(source.entry_path_pattern, parsed_href.path, re.IGNORECASE):
            continue
        seen.add(href)
        published = validate_published_time(
            local_structured_timestamp(anchor, source.homepage_url),
            now,
            window_start,
            skips,
        )
        if published is None:
            continue
        context = clean_text(anchor.parent.get_text(" ") if anchor.parent else title)
        signal = make_signal(
            source,
            title,
            href,
            context,
            published,
            now,
            timestamp_basis="page_structured_time",
            transport_mode="page_fallback",
        )
        if signal is None:
            continue
        signals.append(signal)
        if len(signals) >= max_per_source:
            break
    return signals, skips


def source_status(source: BusinessSource, mode: str, now: datetime) -> dict[str, Any]:
    return {
        "source_id": source.source_id,
        "name": source.name,
        "lane": source.lane,
        "capture_mode": source.capture_mode,
        "ok": False,
        "reachable": False,
        "verified": False,
        "fresh": False,
        "current": False,
        "transport_ok": False,
        "transport_mode": mode,
        "quality_status": "unavailable",
        "item_count": 0,
        "entry_count": 0,
        "verified_timestamp_count": 0,
        "eligible_timestamp_count": 0,
        "timestamp_skips": empty_timestamp_skips(),
        "attempted_urls": [],
        "selected_url": "",
        "latest_verified_published_at": "",
        "freshness_sla_hours": source.freshness_sla_hours,
        "duration_ms": 0,
        "error": "",
        "last_checked_at": now.isoformat().replace("+00:00", "Z"),
        "next_review_at": source.next_review_at,
    }


def record_verified_timestamp(status: dict[str, Any], published: datetime) -> None:
    current = parse_time(status.get("latest_verified_published_at"))
    if current is None or published > current:
        status["latest_verified_published_at"] = published.isoformat().replace("+00:00", "Z")


def finalize_timestamp_status(status: dict[str, Any], signals: list[BusinessSignal], *, empty_error: str) -> None:
    status["item_count"] = len(signals)
    status["reachable"] = bool(status.get("transport_ok"))
    status["verified"] = int(status.get("verified_timestamp_count") or 0) > 0
    status["current"] = int(status.get("eligible_timestamp_count") or 0) > 0
    checked_at = parse_time(status.get("last_checked_at")) or datetime.now(tz=timezone.utc)
    latest_verified = parse_time(status.get("latest_verified_published_at"))
    freshness_sla_hours = max(1, int(status.get("freshness_sla_hours") or 0))
    status["fresh"] = bool(
        latest_verified is not None
        and latest_verified >= checked_at - timedelta(hours=freshness_sla_hours)
    )
    if status["eligible_timestamp_count"] > 0:
        status["ok"] = True
        status["fresh"] = True
        status["quality_status"] = "verified_timestamp"
        return
    if status["verified_timestamp_count"] > 0:
        if status["fresh"]:
            status["ok"] = True
            status["quality_status"] = "no_current_items"
        else:
            status["quality_status"] = "stale_source"
            status["error"] = "latest_verified_item_exceeds_source_sla"
        return
    if status["entry_count"] > 0:
        status["quality_status"] = "unverified_timestamp"
        status["error"] = "entries_without_trustworthy_timestamp"
        return
    status["quality_status"] = "empty_feed" if status["transport_mode"] == "feed" else "unavailable"
    status["error"] = empty_error


def fetch_sitemap_source(
    session: requests.Session,
    source: BusinessSource,
    now: datetime,
    window_start: datetime,
    max_per_source: int,
) -> tuple[list[BusinessSignal], dict[str, Any]]:
    start = time.perf_counter()
    status = source_status(source, "sitemap_page", now)
    budget = FetchBudget()
    candidates_by_url: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for sitemap_url in source.sitemap_urls:
        status["attempted_urls"].append(sitemap_url)
        try:
            response = safe_get(
                session,
                sitemap_url,
                allowed_hosts=allowed_transport_hosts(source),
                budget=budget,
                timeout=max(TIMEOUT, 20),
                max_bytes=MAX_SITEMAP_BYTES,
            )
            rows = sitemap_candidates(response.content, source, budget)
            status["transport_ok"] = True
            for row in rows:
                previous = candidates_by_url.get(row["url"])
                if previous is None or row["last_modified"] > previous["last_modified"]:
                    candidates_by_url[row["url"]] = row
        except Exception as exc:
            errors.append(f"{sitemap_url}: {str(exc)[:180]}")

    candidates = sorted(candidates_by_url.values(), key=lambda row: row["last_modified"], reverse=True)
    status["entry_count"] = len(candidates)
    signals: list[BusinessSignal] = []
    allowed_hosts = allowed_entry_hosts(source)
    for candidate in candidates[: source.candidate_limit]:
        page_url = candidate["url"]
        try:
            response = safe_get(
                session,
                page_url,
                allowed_hosts=allowed_hosts,
                budget=budget,
                timeout=max(TIMEOUT, 20),
                max_bytes=MAX_PAGE_BYTES,
            )
            metadata = publication_page_metadata(response.content, page_url, allowed_hosts)
        except Exception as exc:
            status["timestamp_skips"]["unverified_page"] += 1
            errors.append(f"{page_url}: {str(exc)[:180]}")
            continue
        published = metadata["published"]
        if published <= now:
            status["verified_timestamp_count"] += 1
            status["selected_url"] = status["selected_url"] or page_url
            record_verified_timestamp(status, published)
        published = validate_published_time(published, now, window_start, status["timestamp_skips"])
        if published is None:
            continue
        status["eligible_timestamp_count"] += 1
        signal = make_signal(
            source,
            metadata["title"],
            metadata["url"],
            "",
            published,
            now,
            timestamp_basis="page_structured_time",
            transport_mode="sitemap_page",
        )
        if signal is not None:
            signals.append(signal)
        if len(signals) >= max_per_source:
            break

    finalize_timestamp_status(status, signals, empty_error="sitemap_has_no_reviewed_candidates")
    if not status["ok"] and errors:
        status["error"] = f"{status['error']}; " + " | ".join(errors[:4])
    status["duration_ms"] = int((time.perf_counter() - start) * 1000)
    return signals, status


def fetch_page_detail_source(
    session: requests.Session,
    source: BusinessSource,
    now: datetime,
    window_start: datetime,
    max_per_source: int,
) -> tuple[list[BusinessSignal], dict[str, Any]]:
    start = time.perf_counter()
    status = source_status(source, "page_detail", now)
    budget = FetchBudget()
    status["attempted_urls"].append(source.homepage_url)
    signals: list[BusinessSignal] = []
    errors: list[str] = []
    try:
        response = safe_get(
            session,
            source.homepage_url,
            allowed_hosts=allowed_transport_hosts(source),
            budget=budget,
            timeout=max(TIMEOUT, 20),
            max_bytes=MAX_PAGE_BYTES,
        )
        status["transport_ok"] = True
        soup = BeautifulSoup(response.content, "html.parser")
        allowed_hosts = allowed_entry_hosts(source)
        candidates: list[str] = []
        for anchor in soup.find_all("a", href=True):
            page_url = urljoin(source.homepage_url, str(anchor.get("href") or ""))
            parsed = urlparse(page_url)
            if (parsed.hostname or "").lower() not in allowed_hosts:
                continue
            if source.entry_path_pattern and not re.search(source.entry_path_pattern, parsed.path, re.IGNORECASE):
                continue
            page_url = normalized_public_url(page_url)
            if page_url not in candidates:
                candidates.append(page_url)
        status["entry_count"] = len(candidates)
        for page_url in candidates[: source.candidate_limit]:
            try:
                page = safe_get(
                    session,
                    page_url,
                    allowed_hosts=allowed_hosts,
                    budget=budget,
                    timeout=max(TIMEOUT, 20),
                    max_bytes=MAX_PAGE_BYTES,
                )
                metadata = publication_page_metadata(page.content, page_url, allowed_hosts)
            except Exception as exc:
                status["timestamp_skips"]["unverified_page"] += 1
                errors.append(f"{page_url}: {str(exc)[:180]}")
                continue
            published = metadata["published"]
            if published <= now:
                status["verified_timestamp_count"] += 1
                status["selected_url"] = status["selected_url"] or page_url
                record_verified_timestamp(status, published)
            published = validate_published_time(published, now, window_start, status["timestamp_skips"])
            if published is None:
                continue
            status["eligible_timestamp_count"] += 1
            signal = make_signal(
                source,
                metadata["title"],
                metadata["url"],
                "",
                published,
                now,
                timestamp_basis="page_structured_time",
                transport_mode="page_detail",
            )
            if signal is not None:
                signals.append(signal)
            if len(signals) >= max_per_source:
                break
    except Exception as exc:
        errors.append(f"{source.homepage_url}: {str(exc)[:220]}")

    finalize_timestamp_status(status, signals, empty_error="page_detail_has_no_reviewed_candidates")
    if not status["ok"] and errors:
        status["error"] = f"{status['error']}; " + " | ".join(errors[:4])
    status["duration_ms"] = int((time.perf_counter() - start) * 1000)
    return signals, status


def fetch_feed(session: requests.Session, source: BusinessSource, now: datetime, window_start: datetime, max_per_source: int) -> tuple[list[BusinessSignal], dict[str, Any]]:
    start = time.perf_counter()
    status = source_status(source, "feed", now)
    feed_errors: list[str] = []
    saw_entries = False
    candidates = source.feed_candidates or (source.feed_url,)
    allowed_hosts = allowed_entry_hosts(source)
    transport_hosts = allowed_transport_hosts(source)
    budget = FetchBudget()
    candidate_results: list[dict[str, Any]] = []

    for candidate_index, feed_url in enumerate(candidates):
        status["attempted_urls"].append(feed_url)
        candidate_skips = empty_timestamp_skips()
        candidate_signals: list[BusinessSignal] = []
        verified_timestamp_count = 0
        eligible_timestamp_count = 0
        latest_verified_timestamp: datetime | None = None
        try:
            resp = safe_get(
                session,
                feed_url,
                allowed_hosts=transport_hosts,
                budget=budget,
                timeout=TIMEOUT,
                max_bytes=MAX_FEED_BYTES,
                allow_http_initial=feed_url.startswith("http://"),
            )
            status["transport_ok"] = True
            if feedparser is not None:
                entries = list(feedparser.parse(resp.content).entries)
            else:
                soup = BeautifulSoup(resp.text, "xml")
                entries = [
                    {
                        "title": clean_text(item.find("title")),
                        "link": clean_text(item.find("link")),
                        "summary": clean_text(item.find("description") or item.find("summary")),
                        "published": clean_text(item.find("pubDate") or item.find("published") or item.find("updated")),
                    }
                    for item in soup.find_all(["item", "entry"])
                ]
            if not entries:
                feed_errors.append(f"{feed_url}: feed_returned_no_entries")
                continue
            saw_entries = True
            for entry in entries[: max_per_source * 3]:
                title = clean_text(entry.get("title"))
                raw_url = clean_text(entry.get("link") or entry.get("id"))
                url = canonical_entry_url(source, feed_url, raw_url)
                parsed_url = urlparse(url)
                if (
                    not title
                    or not raw_url
                    or parsed_url.scheme != "https"
                    or (parsed_url.hostname or "").lower() not in allowed_hosts
                ):
                    continue
                summary_text = clean_text(entry.get("summary") or entry.get("description") or entry.get("content", [{}])[0].get("value") if isinstance(entry.get("content"), list) and entry.get("content") else "")
                published_value = entry.get("published") or entry.get("updated") or entry.get("created")
                parsed_timestamp = parse_time(published_value)
                if parsed_timestamp is None or parsed_timestamp > now:
                    validate_published_time(published_value, now, window_start, candidate_skips)
                    continue
                needs_page_cross_check = source.require_entry_page_cross_check and (
                    parsed_timestamp >= window_start or verified_timestamp_count == 0
                )
                if needs_page_cross_check:
                    try:
                        page = safe_get(
                            session,
                            url,
                            allowed_hosts=allowed_hosts,
                            budget=budget,
                            timeout=max(TIMEOUT, 20),
                            max_bytes=MAX_PAGE_BYTES,
                        )
                        page_metadata = publication_page_metadata(page.content, url, allowed_hosts)
                        if clean_text(page_metadata["title"]) != title or page_metadata["published"] != parsed_timestamp:
                            candidate_skips["conflicted_timestamp"] += 1
                            continue
                    except Exception as exc:
                        candidate_skips["unverified_page"] += 1
                        feed_errors.append(f"{url}: {str(exc)[:180]}")
                        continue
                if not source.require_entry_page_cross_check or needs_page_cross_check:
                    verified_timestamp_count += 1
                    latest_verified_timestamp = max(latest_verified_timestamp or parsed_timestamp, parsed_timestamp)
                published = validate_published_time(parsed_timestamp, now, window_start, candidate_skips)
                if published is None:
                    continue
                eligible_timestamp_count += 1
                signal = make_signal(
                    source,
                    title,
                    url,
                    summary_text,
                    published,
                    now,
                    timestamp_basis="feed_published",
                    transport_mode="feed",
                )
                if signal is not None:
                    candidate_signals.append(signal)
                if len(candidate_signals) >= max_per_source:
                    break
            candidate_results.append(
                {
                    "candidate_index": candidate_index,
                    "feed_url": feed_url,
                    "entry_count": len(entries),
                    "signals": candidate_signals,
                    "verified_timestamp_count": verified_timestamp_count,
                    "eligible_timestamp_count": eligible_timestamp_count,
                    "latest_verified_timestamp": latest_verified_timestamp,
                    "timestamp_skips": candidate_skips,
                }
            )
            if verified_timestamp_count == 0:
                feed_errors.append(f"{feed_url}: feed_entries_without_trustworthy_timestamp")
        except Exception as exc:
            feed_errors.append(f"{feed_url}: {str(exc)[:220]}")

    for result in candidate_results:
        for reason, count in result["timestamp_skips"].items():
            status["timestamp_skips"][reason] += count

    trustworthy_results = [row for row in candidate_results if row["verified_timestamp_count"] > 0]
    selected = None
    if trustworthy_results:
        selected = max(
            trustworthy_results,
            key=lambda row: (
                row["eligible_timestamp_count"] > 0,
                row["latest_verified_timestamp"] or datetime.min.replace(tzinfo=timezone.utc),
                -row["candidate_index"],
            ),
        )

    signals: list[BusinessSignal] = []
    if selected is not None:
        signals = selected["signals"]
        status["entry_count"] = selected["entry_count"]
        status["selected_url"] = selected["feed_url"]
        status["verified_timestamp_count"] = selected["verified_timestamp_count"]
        status["eligible_timestamp_count"] = selected["eligible_timestamp_count"]
        latest_verified = selected["latest_verified_timestamp"]
        if latest_verified is not None:
            record_verified_timestamp(status, latest_verified)
    elif candidate_results:
        status["entry_count"] = sum(row["entry_count"] for row in candidate_results)

    if not saw_entries and selected is None:
        try:
            fallback_signals, skips = fetch_page_fallback(
                session,
                source,
                now,
                window_start,
                max_per_source,
                budget,
            )
            if fallback_signals:
                signals = fallback_signals
                status["transport_ok"] = True
                status["transport_mode"] = "page_fallback"
                status["timestamp_skips"] = skips
                status["verified_timestamp_count"] = len(signals)
                status["eligible_timestamp_count"] = len(signals)
                for signal in signals:
                    published = parse_time(signal.published_at)
                    if published is not None:
                        record_verified_timestamp(status, published)
        except Exception as exc:
            feed_errors.append(f"{source.homepage_url}: {str(exc)[:220]}")

    finalize_timestamp_status(status, signals, empty_error="feed_returned_no_entries")
    if not status["ok"] and feed_errors:
        status["error"] = f"{status['error']}; " + " | ".join(feed_errors[:4])
    status["duration_ms"] = int((time.perf_counter() - start) * 1000)
    return signals, status


def fetch_source_evidence(
    session: requests.Session,
    source: BusinessSource,
    now: datetime,
    window_start: datetime,
    max_per_source: int,
) -> tuple[list[BusinessSignal], dict[str, Any]]:
    if source.capture_mode == "sitemap":
        return fetch_sitemap_source(session, source, now, window_start, max_per_source)
    if source.capture_mode == "page_detail":
        return fetch_page_detail_source(session, source, now, window_start, max_per_source)
    if source.capture_mode == "manual":
        status = source_status(source, "manual", now)
        status["quality_status"] = "manual_review_required"
        status["error"] = "no_stable_public_timestamped_surface"
        return [], status
    return fetch_feed(session, source, now, window_start, max_per_source)


def dedupe_signals(signals: list[BusinessSignal]) -> list[BusinessSignal]:
    seen: dict[str, BusinessSignal] = {}
    for signal in signals:
        key = re.sub(r"[^a-z0-9]+", " ", signal.title.lower()).strip()
        key = key[:90] or signal.url
        current = seen.get(key)
        if current is None or signal.total_score > current.total_score:
            seen[key] = signal
    return sorted(seen.values(), key=lambda item: (item.total_score, item.published_at), reverse=True)


def cluster_key(signal: BusinessSignal) -> str:
    if "OPC" in signal.business_model_tags or signal.lane == "opc":
        return "opc"
    if "Counter Signal" in signal.business_model_tags:
        return "counter_signal"
    if signal.lane == "authority":
        return "authority_trust"
    if "Enterprise AI Workflow" in signal.business_model_tags:
        return "enterprise_workflow"
    if "Founder Case" in signal.business_model_tags:
        return "founder_case"
    return "business_model"


CLUSTER_COPY = {
    "opc": {
        "thesis": "AI leverage is making one-person and tiny-team companies a credible strategic archetype.",
        "action": "Turn the strongest case into a Yuanli OPC teaching module with leverage, distribution, and monetization explicitly mapped.",
        "mapping": ["yuanli_asset", "yuanli_startup", "profit_container"],
    },
    "business_model": {
        "thesis": "AI-native business models are shifting from tool features to workflows, services, and outcome economics.",
        "action": "Extract pricing and workflow patterns into the Yuanli business model library.",
        "mapping": ["yuanli_startup", "yuanli_os", "profit_container"],
    },
    "authority_trust": {
        "thesis": "Business-school and consulting evidence strengthens the trust layer behind Yuanli IP claims.",
        "action": "Use these authority-backed signals as FTF proof points before making public claims.",
        "mapping": ["ftf_trust", "yuanli_asset", "yuanli_startup"],
    },
    "enterprise_workflow": {
        "thesis": "Enterprise AI value is moving toward operating-model redesign rather than isolated copilots.",
        "action": "Map each workflow case to the Yuanli OS organs: soul, memory, judgment, hands.",
        "mapping": ["yuanli_os", "ftf_trust"],
    },
    "founder_case": {
        "thesis": "Founder stories provide the most reusable proof layer for Yuanli IP trust-building.",
        "action": "Convert high-score founder cases into FTF credible story assets.",
        "mapping": ["ftf_trust", "yuanli_asset", "wealth_flywheel"],
    },
    "counter_signal": {
        "thesis": "AI adoption counter-signals reveal where Yuanli claims need sharper proof and risk language.",
        "action": "Add these counter-signals to sales objections and content credibility checks.",
        "mapping": ["ftf_trust", "yuanli_os"],
    },
}


def build_clusters(signals: list[BusinessSignal]) -> list[dict[str, Any]]:
    buckets: dict[str, list[BusinessSignal]] = {}
    for signal in signals:
        buckets.setdefault(cluster_key(signal), []).append(signal)

    clusters: list[dict[str, Any]] = []
    for key, rows in buckets.items():
        rows = sorted(rows, key=lambda item: item.total_score, reverse=True)[:12]
        if not rows:
            continue
        copy = CLUSTER_COPY[key]
        source_count = len({row.source_id for row in rows})
        importance = min(100, round(sum(row.total_score for row in rows[:5]) / min(5, len(rows)) + min(12, source_count * 2)))
        clusters.append(
            {
                "cluster_id": stable_id(key, ",".join(row.signal_id for row in rows[:6]), prefix="biz_cluster"),
                "thesis": copy["thesis"],
                "lane": key,
                "signal_ids": [row.signal_id for row in rows],
                "source_count": source_count,
                "importance_score": importance,
                "confidence": "high" if source_count >= 4 else "medium" if source_count >= 2 else "watch",
                "yuanli_mapping": copy["mapping"],
                "why_it_matters": f"{len(rows)} English evidence items from {source_count} sources connect this pattern to Yuanli IP.",
                "counter_evidence": "See counter_signal cluster for adoption, ROI, and trust risks." if key != "counter_signal" else "Counter-signals are the evidence, not a rejection of the thesis.",
                "recommended_action": copy["action"],
                "evidence_refs": [row.signal_id for row in rows[:5]],
                "top_sources": [{"source": row.source_name, "title": row.title, "url": row.url} for row in rows[:5]],
            }
        )
    return sorted(clusters, key=lambda item: item["importance_score"], reverse=True)


def build_case_bank(signals: list[BusinessSignal]) -> list[dict[str, Any]]:
    candidates = [
        signal
        for signal in signals
        if signal.lane == "opc"
        or "OPC" in signal.business_model_tags
        or "Founder Case" in signal.business_model_tags
        or signal.case_concreteness_score >= 8
    ]
    cases: list[dict[str, Any]] = []
    for signal in sorted(candidates, key=lambda item: (item.opc_fit_score + item.case_concreteness_score, item.total_score), reverse=True)[:24]:
        company = next((entity for entity in signal.entities if entity != signal.source_name), signal.source_name)
        cases.append(
            {
                "case_id": stable_id(signal.signal_id, "case", prefix="opc_case"),
                "company": company,
                "founder": "",
                "source_refs": [signal.signal_id],
                "url": signal.url,
                "title": signal.title,
                "business_model": ", ".join(signal.business_model_tags[:3]),
                "ai_leverage": "AI leverage inferred from source/title tags; verify in full article before using as a public claim.",
                "monetization": "To be extracted from the linked source.",
                "team_size_signal": "one-person/tiny-team fit" if signal.opc_fit_score >= 6 else "team size not explicit",
                "distribution_channel": signal.source_name,
                "reusable_lesson": "Use this as a Yuanli case atom: actor, leverage, distribution, monetization, and proof.",
                "yuanli_mapping": signal.yuanli_tags,
                "score": signal.total_score,
            }
        )
    return cases


def build_brief(clusters: list[dict[str, Any]], signals: list[BusinessSignal], generated_at: str) -> list[dict[str, Any]]:
    by_id = {signal.signal_id: signal for signal in signals}
    brief: list[dict[str, Any]] = []
    for rank, cluster in enumerate(clusters[:5], start=1):
        evidence = [by_id[sid] for sid in cluster["evidence_refs"] if sid in by_id]
        brief.append(
            {
                "brief_id": stable_id(cluster["cluster_id"], generated_at, prefix="biz_brief"),
                "rank": rank,
                "title": cluster["thesis"],
                "judgment": cluster["why_it_matters"],
                "evidence_refs": [item.signal_id for item in evidence],
                "evidence_titles": [item.title for item in evidence[:3]],
                "risk_level": "medium" if cluster["lane"] == "counter_signal" else "low",
                "yuanli_mapping": cluster["yuanli_mapping"],
                "recommended_action": cluster["recommended_action"],
                "generated_at": generated_at,
            }
        )
    return brief


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_catalog(statuses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    status_by_id = {row["source_id"]: row for row in statuses}
    catalog: list[dict[str, Any]] = []
    for source in SOURCES:
        status = status_by_id.get(source.source_id, {})
        row = asdict(source)
        row["health_status"] = str(status.get("quality_status") or "unknown")
        row["reachable"] = bool(status.get("reachable"))
        row["verified"] = bool(status.get("verified"))
        row["fresh"] = bool(status.get("fresh"))
        row["current"] = bool(status.get("current"))
        row["latest_verified_published_at"] = str(status.get("latest_verified_published_at") or "")
        row["last_checked_at"] = str(status.get("last_checked_at") or "")
        row["latest_error"] = str(status.get("error") or "")
        catalog.append(row)
    return catalog


def run(output_dir: Path, window_hours: int, max_items: int, max_per_source: int) -> dict[str, Any]:
    if window_hours != DEFAULT_WINDOW_HOURS:
        raise ValueError(
            f"business-latest-24h.json requires window_hours={DEFAULT_WINDOW_HOURS}; got {window_hours}"
        )
    now = datetime.now(tz=timezone.utc)
    window_start = now - timedelta(hours=window_hours)

    all_signals: list[BusinessSignal] = []
    statuses: list[dict[str, Any]] = []

    def fetch_source(source: BusinessSource) -> tuple[list[BusinessSignal], dict[str, Any]]:
        session = requests.Session()
        session.headers.update({"User-Agent": UA, "Accept": "application/rss+xml, application/xml, text/xml, text/html;q=0.9, */*;q=0.8"})
        return fetch_source_evidence(session, source, now, window_start, max_per_source)

    with ThreadPoolExecutor(max_workers=8) as executor:
        future_map = {executor.submit(fetch_source, source): source for source in SOURCES}
        for future in as_completed(future_map):
            source = future_map[future]
            try:
                signals, status = future.result()
            except Exception as exc:
                signals = []
                status = source_status(source, "worker", now)
                status["error"] = f"worker_failed: {str(exc)[:500]}"
            all_signals.extend(signals)
            statuses.append(status)

    signals = dedupe_signals(all_signals)[:max_items]
    if any(not signal.timestamp_basis or not signal.transport_mode for signal in signals):
        raise RuntimeError("business evidence item missing trustworthy timestamp provenance")
    generated_at = now.isoformat().replace("+00:00", "Z")
    clusters = build_clusters(signals)
    case_bank = build_case_bank(signals)
    brief = build_brief(clusters, signals, generated_at)
    catalog = build_catalog(statuses)
    status_payload = {
        "generated_at": generated_at,
        "window_hours": window_hours,
        "source_count": len(SOURCES),
        "successful_sources": sum(1 for row in statuses if row.get("ok")),
        "failed_sources": sum(1 for row in statuses if not row.get("ok")),
        "reachable_sources": sum(1 for row in statuses if row.get("reachable")),
        "verified_sources": sum(1 for row in statuses if row.get("verified")),
        "fresh_sources": sum(1 for row in statuses if row.get("fresh")),
        "current_sources": sum(1 for row in statuses if row.get("current")),
        "stale_sources": sum(1 for row in statuses if row.get("quality_status") == "stale_source"),
        "automated_source_count": sum(1 for source in SOURCES if source.capture_mode != "manual"),
        "automated_failed_sources": sum(
            1 for row in statuses if row.get("capture_mode") != "manual" and not row.get("ok")
        ),
        "manual_review_sources": sum(1 for row in statuses if row.get("capture_mode") == "manual"),
        "item_count": len(signals),
        "timestamp_skips": {
            reason: sum(int(row.get("timestamp_skips", {}).get(reason, 0)) for row in statuses)
            for reason in TIMESTAMP_SKIP_REASONS
        },
        "sources": statuses,
    }

    signal_rows = [asdict(signal) for signal in signals]
    write_json(output_dir / "business-source-catalog.json", catalog)
    write_json(output_dir / "business-latest-24h.json", {"generated_at": generated_at, "window_hours": window_hours, "items": signal_rows})
    write_json(output_dir / "business-source-status.json", status_payload)
    write_json(output_dir / "business-stories-merged.json", {"generated_at": generated_at, "clusters": clusters})
    write_json(output_dir / "business-daily-brief.json", {"generated_at": generated_at, "brief": brief})
    write_json(output_dir / "business-case-bank.json", {"generated_at": generated_at, "cases": case_bank})
    return {
        "generated_at": generated_at,
        "signals": len(signal_rows),
        "clusters": len(clusters),
        "brief": len(brief),
        "cases": len(case_bank),
        "successful_sources": status_payload["successful_sources"],
        "failed_sources": status_payload["failed_sources"],
        "automated_failed_sources": status_payload["automated_failed_sources"],
        "manual_review_sources": status_payload["manual_review_sources"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="data", type=Path)
    parser.add_argument("--window-hours", default=DEFAULT_WINDOW_HOURS, type=int)
    parser.add_argument("--max-items", default=150, type=int)
    parser.add_argument("--max-per-source", default=10, type=int)
    args = parser.parse_args()
    summary = run(args.output_dir, args.window_hours, args.max_items, args.max_per_source)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
