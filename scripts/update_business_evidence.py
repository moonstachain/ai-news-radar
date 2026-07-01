#!/usr/bin/env python3
"""Build the English-only business evidence layer for Yuanli IP radar.

The layer is deliberately separate from the 24h AI news feed. It reads only
public English sources and publishes compact evidence artifacts for AI business
models, one-person companies, founder cases, and counter-signals.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from bs4 import MarkupResemblesLocatorWarning

try:
    import feedparser
except Exception:  # pragma: no cover - optional dependency branch
    feedparser = None


UA = "AI-News-Radar-Business-Evidence/1.0 (+https://github.com/moonstachain/ai-news-radar)"
TIMEOUT = 6
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


SOURCES: list[BusinessSource] = [
    BusinessSource("mckinsey_ai", "McKinsey / QuantumBlack", "https://www.mckinsey.com/capabilities/quantumblack/our-insights", "https://www.mckinsey.com/featured-insights/rss", "authority", "tier_1"),
    BusinessSource("bcg_ai", "BCG AI Insights", "https://www.bcg.com/capabilities/artificial-intelligence/insights", "https://www.bcg.com/rss", "authority", "tier_1"),
    BusinessSource("bain_insights", "Bain Insights", "https://www.bain.com/insights/", "https://www.bain.com/insights/rss/", "authority", "tier_1"),
    BusinessSource("hbr", "Harvard Business Review", "https://hbr.org/", "https://feeds.hbr.org/harvardbusiness", "authority", "tier_1"),
    BusinessSource("mit_smr", "MIT Sloan Management Review", "https://sloanreview.mit.edu/", "https://sloanreview.mit.edu/feed/", "authority", "tier_1"),
    BusinessSource("knowledge_wharton", "Knowledge at Wharton", "https://knowledge.wharton.upenn.edu/", "https://knowledge.wharton.upenn.edu/feed/", "authority", "tier_2"),
    BusinessSource("yc_blog", "Y Combinator Blog", "https://www.ycombinator.com/blog", "https://www.ycombinator.com/blog/rss", "startup_vc", "tier_1"),
    BusinessSource("a16z", "a16z", "https://a16z.com/ai/", "https://a16z.com/feed/", "startup_vc", "tier_1"),
    BusinessSource("first_round", "First Round Review", "https://review.firstround.com/", "https://review.firstround.com/rss/", "startup_vc", "tier_1"),
    BusinessSource("lenny", "Lenny's Newsletter", "https://www.lennysnewsletter.com/", "https://www.lennysnewsletter.com/feed", "startup_vc", "tier_2"),
    BusinessSource("generalist", "The Generalist", "https://www.generalist.com/", "https://www.generalist.com/feed", "startup_vc", "tier_2"),
    BusinessSource("not_boring", "Not Boring", "https://www.notboring.co/", "https://www.notboring.co/feed", "startup_vc", "tier_2"),
    BusinessSource("cbinsights", "CB Insights", "https://www.cbinsights.com/research/", "https://www.cbinsights.com/research/feed/", "startup_vc", "tier_2"),
    BusinessSource("indie_hackers", "Indie Hackers", "https://www.indiehackers.com/", "https://www.indiehackers.com/feed.xml", "opc", "tier_2"),
    BusinessSource("starter_story", "Starter Story", "https://www.starterstory.com/", "https://www.starterstory.com/feed", "opc", "tier_2"),
    BusinessSource("microconf", "MicroConf", "https://microconf.com/", "https://microconf.com/feed", "opc", "tier_2"),
    BusinessSource("tinyseed", "TinySeed", "https://tinyseed.com/", "https://tinyseed.com/feed", "opc", "tier_2"),
    BusinessSource("bootstrapped_founder", "The Bootstrapped Founder", "https://thebootstrappedfounder.com/", "https://thebootstrappedfounder.com/feed.xml", "opc", "tier_2"),
    BusinessSource("levelsio", "levels.io", "https://levels.io/", "https://levels.io/rss/", "opc", "tier_2"),
    BusinessSource("latent_space", "Latent Space", "https://www.latent.space/", "https://www.latent.space/feed", "ai_commercialization", "tier_2"),
    BusinessSource("ai_engineer", "AI Engineer", "https://www.ai.engineer/", "https://www.ai.engineer/feed", "ai_commercialization", "tier_2"),
    BusinessSource("the_batch", "The Batch", "https://www.deeplearning.ai/the-batch/", "https://www.deeplearning.ai/the-batch/rss/", "ai_commercialization", "tier_2"),
    BusinessSource("openai_news", "OpenAI News", "https://openai.com/news/", "https://openai.com/news/rss.xml", "ai_commercialization", "tier_1"),
    BusinessSource("anthropic_news", "Anthropic News", "https://www.anthropic.com/news", "https://www.anthropic.com/news/rss.xml", "ai_commercialization", "tier_1"),
    BusinessSource("github_blog", "GitHub Blog", "https://github.blog/", "https://github.blog/feed/", "ai_commercialization", "tier_1"),
    BusinessSource("huggingface_blog", "Hugging Face Blog", "https://huggingface.co/blog", "https://huggingface.co/blog/feed.xml", "ai_commercialization", "tier_2"),
]


def now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z")


def parse_time(value: Any, fallback: datetime) -> datetime:
    if not value:
        return fallback
    if isinstance(value, datetime):
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
        return fallback


def stable_id(*parts: str, prefix: str = "biz") -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


def clean_text(text: Any) -> str:
    if text is None:
        return ""
    collapsed = re.sub(r"\s+", " ", BeautifulSoup(str(text), "html.parser").get_text(" "))
    return collapsed.strip()


def host(url: str) -> str:
    return urlparse(url).netloc.replace("www.", "")


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


def make_signal(source: BusinessSource, title: str, url: str, summary_text: str, published: datetime, now: datetime) -> BusinessSignal | None:
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


def fetch_page_fallback(session: requests.Session, source: BusinessSource, now: datetime, max_per_source: int) -> list[BusinessSignal]:
    resp = session.get(source.homepage_url, timeout=TIMEOUT)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    signals: list[BusinessSignal] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a"):
        title = clean_text(anchor.get_text(" "))
        href = str(anchor.get("href") or "").strip()
        if not title or len(title) < 18 or not href:
            continue
        if href.startswith("/"):
            parsed = urlparse(source.homepage_url)
            href = f"{parsed.scheme}://{parsed.netloc}{href}"
        if not href.startswith("http") or href in seen:
            continue
        seen.add(href)
        context = clean_text(anchor.parent.get_text(" ") if anchor.parent else title)
        signal = make_signal(source, title, href, context, now, now)
        if signal is None:
            continue
        signals.append(signal)
        if len(signals) >= max_per_source:
            break
    return signals


def fetch_feed(session: requests.Session, source: BusinessSource, now: datetime, window_start: datetime, max_per_source: int) -> tuple[list[BusinessSignal], dict[str, Any]]:
    start = time.perf_counter()
    status = {
        "source_id": source.source_id,
        "name": source.name,
        "lane": source.lane,
        "ok": False,
        "item_count": 0,
        "duration_ms": 0,
        "error": "",
        "last_checked_at": now_iso(),
    }
    signals: list[BusinessSignal] = []
    try:
        resp = session.get(source.feed_url, timeout=TIMEOUT)
        resp.raise_for_status()
        if feedparser is not None:
            parsed = feedparser.parse(resp.content)
            entries = list(parsed.entries)
        else:
            soup = BeautifulSoup(resp.text, "xml")
            entries = []
            for item in soup.find_all(["item", "entry"]):
                entries.append(
                    {
                        "title": clean_text(item.find("title")),
                        "link": clean_text(item.find("link")),
                        "summary": clean_text(item.find("description") or item.find("summary")),
                        "published": clean_text(item.find("pubDate") or item.find("published") or item.find("updated")),
                    }
                )
        for entry in entries[: max_per_source * 3]:
            title = clean_text(entry.get("title"))
            url = clean_text(entry.get("link") or entry.get("id"))
            if not title or not url:
                continue
            summary_text = clean_text(entry.get("summary") or entry.get("description") or entry.get("content", [{}])[0].get("value") if isinstance(entry.get("content"), list) and entry.get("content") else "")
            published = parse_time(entry.get("published") or entry.get("updated") or entry.get("created"), now)
            # Public sources often omit dates or publish evergreen case archives. Keep
            # relevant evergreen evidence, but mark freshness through scoring.
            if published < window_start and source.lane not in {"opc", "startup_vc"}:
                continue
            signal = make_signal(source, title, url, summary_text, published, now)
            if signal is None:
                continue
            signals.append(signal)
            if len(signals) >= max_per_source:
                break
        status["ok"] = True
        status["item_count"] = len(signals)
    except Exception as exc:
        feed_error = str(exc)[:500]
        try:
            signals = fetch_page_fallback(session, source, now, max_per_source)
            status["ok"] = True
            status["item_count"] = len(signals)
            status["error"] = f"feed_failed_page_fallback: {feed_error}" if signals else f"feed_failed_empty_page_fallback: {feed_error}"
        except Exception as page_exc:
            status["error"] = f"feed_failed: {feed_error}; page_failed: {str(page_exc)[:220]}"
    status["duration_ms"] = int((time.perf_counter() - start) * 1000)
    return signals, status


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
        row["health_status"] = "ok" if status.get("ok") else "failed" if status else "unknown"
        row["last_checked_at"] = str(status.get("last_checked_at") or "")
        row["latest_error"] = str(status.get("error") or "")
        catalog.append(row)
    return catalog


def run(output_dir: Path, window_hours: int, max_items: int, max_per_source: int) -> dict[str, Any]:
    now = datetime.now(tz=timezone.utc)
    window_start = now - timedelta(hours=window_hours)

    all_signals: list[BusinessSignal] = []
    statuses: list[dict[str, Any]] = []

    def fetch_source(source: BusinessSource) -> tuple[list[BusinessSignal], dict[str, Any]]:
        session = requests.Session()
        session.headers.update({"User-Agent": UA, "Accept": "application/rss+xml, application/xml, text/xml, text/html;q=0.9, */*;q=0.8"})
        return fetch_feed(session, source, now, window_start, max_per_source)

    with ThreadPoolExecutor(max_workers=8) as executor:
        future_map = {executor.submit(fetch_source, source): source for source in SOURCES}
        for future in as_completed(future_map):
            source = future_map[future]
            try:
                signals, status = future.result()
            except Exception as exc:
                signals = []
                status = {
                    "source_id": source.source_id,
                    "name": source.name,
                    "lane": source.lane,
                    "ok": False,
                    "item_count": 0,
                    "duration_ms": 0,
                    "error": f"worker_failed: {str(exc)[:500]}",
                    "last_checked_at": now_iso(),
                }
            all_signals.extend(signals)
            statuses.append(status)

    signals = dedupe_signals(all_signals)[:max_items]
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
        "item_count": len(signals),
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
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="data", type=Path)
    parser.add_argument("--window-hours", default=72, type=int)
    parser.add_argument("--max-items", default=150, type=int)
    parser.add_argument("--max-per-source", default=10, type=int)
    args = parser.parse_args()
    summary = run(args.output_dir, args.window_hours, args.max_items, args.max_per_source)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
