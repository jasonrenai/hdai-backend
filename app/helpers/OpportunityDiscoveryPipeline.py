"""
Multi-hop opportunity discovery pipeline (Untitled-1 flow).

1. Scrape SERP / source URL
2. Classify: direct_opportunity | blog_or_aggregator | not_speaking
3. If blog: resolve grounded official event URLs and hop to them
4. Confirm speaking opportunity on the resolved page
5. Require exact day-precision start/end dates (no guessing)
6. Extract metrics, then resolve/verify submission (Speakers-first) or contact fallback
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse

from openai import OpenAI

from app.agents.EventDetailEnricherAgent import EventDetailEnricherAgent
from app.helpers.OpportunityQualifier import (
    filter_opportunities_verified_on_official_site,
    qualify_opportunities_batch,
    _urls_same_page,
)
from app.helpers.OpportunitySubmissionResolver import OpportunitySubmissionResolver
from app.helpers.RapidAPIScraper import RapidAPIScraper
from app.helpers.SpeakingOpportunityExtractor import (
    SpeakingOpportunityExtractor,
    _parse_date_to_iso,
    _is_future_or_today,
)
from app.models.Opportunity import OpportunityModel, opportunity_dedupe_key

logger = logging.getLogger(__name__)

PAGE_TYPE_DIRECT = "direct_opportunity"
PAGE_TYPE_BLOG = "blog_or_aggregator"
PAGE_TYPE_NOT_SPEAKING = "not_speaking"

MAX_HOP_URLS = 10
MAX_CLASSIFY_CONTENT = 8000
MAX_RESOLVE_CONTENT = 10000
DESCRIPTION_MAX_LENGTH = 500
DESCRIPTION_FALLBACK = "Scraped page"

# Domains that are almost never the official opportunity page
_AGGREGATOR_HOST_HINTS = (
    "facebook.com",
    "fb.com",
    "linkedin.com",
    "twitter.com",
    "x.com",
    "instagram.com",
    "medium.com",
    "reddit.com",
    "quora.com",
    "youtube.com",
    "tiktok.com",
)

_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]*)\]\((https?://[^)\s]+)\)", re.IGNORECASE)
_BARE_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)

CLASSIFY_SYSTEM_PROMPT = """You classify a scraped webpage for speaking-opportunity discovery.

Return ONLY valid JSON with exactly these keys:
- page_type: one of "direct_opportunity", "blog_or_aggregator", "not_speaking"
- reason: short evidence-based explanation

Definitions:
- direct_opportunity: This page itself hosts a speaking opportunity, call for speakers, speaker application, CFP, or event page where an external professional can speak or apply to speak for ONE primary event/site.
- blog_or_aggregator: A blog post, news article, listicle, guide, social post, or directory that lists or mentions speaking opportunities / events but is not the official opportunity page for those events.
- not_speaking: No speaking opportunity signals (attend-only content, unrelated page, dead/empty page, etc.).

Do not invent facts."""

RESOLVE_URLS_SYSTEM_PROMPT = """You extract official event / speaking-opportunity URLs from a blog, listicle, guide, or aggregator page.

Return ONLY valid JSON:
{"candidates":[{"event_name":"...","url":"https://..."}]}

Rules:
- url MUST appear exactly in the provided groundedUrls list (copy the URL string exactly).
- Prefer URLs that look like call-for-speakers / apply / CFP / speakers pages when present.
- Otherwise prefer the official event homepage for each distinct event that mentions speaking opportunities.
- Skip attend-only ticket/registration pages, social shares, tracking links, the source page itself, and unrelated navigation.
- event_name must come from the page text; use empty string if unclear. Do not invent names or URLs.
- If none are grounded, return {"candidates":[]}.
- At most 10 candidates."""


def is_pdf_url(url: str) -> bool:
    if not url or not isinstance(url, str):
        return False
    path = (urlparse(url.strip()).path or "").rstrip("/")
    return path.lower().endswith(".pdf")


def _normalize_url_key(u: str) -> Tuple[str, str]:
    p = urlparse((u or "").strip())
    netloc = (p.netloc or "").lower()
    path = (p.path or "").rstrip("/").lower()
    return netloc, path


def _collect_grounded_urls(content: str, discovered_links: List[Any], base_url: str) -> List[str]:
    """URLs explicitly present in markdown/bare text or returned by the scraper."""
    seen: Set[str] = set()
    out: List[str] = []

    def add(raw: str) -> None:
        u = (raw or "").strip().rstrip(".,);]")
        if not u:
            return
        if u.startswith("/") and base_url:
            u = urljoin(base_url, u)
        if not u.startswith("http"):
            return
        if is_pdf_url(u):
            return
        key = _normalize_url_key(u)
        if key in seen:
            return
        seen.add(key)
        out.append(u)

    for m in _MARKDOWN_LINK_RE.finditer(content or ""):
        add(m.group(2))
    for m in _BARE_URL_RE.finditer(content or ""):
        add(m.group(0))

    for link in discovered_links or []:
        if isinstance(link, dict):
            add(str(link.get("url") or link.get("href") or link.get("link") or ""))
        else:
            add(str(link or ""))

    return out


def _llm_json_object(text: str) -> Optional[Dict[str, Any]]:
    text = (text or "").strip()
    if not text:
        return None
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1]) if len(lines) > 2 else text
        if text.startswith("json"):
            text = text[4:].strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            try:
                data = json.loads(match.group())
                return data if isinstance(data, dict) else None
            except json.JSONDecodeError:
                pass
    return None


class OpportunityDiscoveryPipeline:
    """Runs the multi-hop scrape → classify → resolve → verify → submit flow."""

    def __init__(
        self,
        rapidapi_scraper: RapidAPIScraper = None,
        delay_seconds: float = 0,
        target_audiences: Optional[List[str]] = None,
    ):
        self.scraper = rapidapi_scraper or RapidAPIScraper(delay_seconds=delay_seconds)
        self.extractor = SpeakingOpportunityExtractor(target_audiences=target_audiences)
        self.enricher = EventDetailEnricherAgent(
            rapidapi_scraper=self.scraper,
            target_audiences=target_audiences,
        )
        self.submission_resolver = OpportunitySubmissionResolver(rapidapi_scraper=self.scraper)

    def run(self, url: str, delay_seconds: float = 0) -> Optional[dict]:
        """
        Returns dict with keys: source_name, description, opportunities; or None on scrape failure.
        """
        if is_pdf_url(url):
            return None

        if delay_seconds and hasattr(self.scraper, "delay_seconds"):
            self.scraper.delay_seconds = delay_seconds

        result = self.scraper.scrape(url)
        if not result.get("success"):
            return None
        data = result.get("data") or {}
        content = (data.get("content") or "").strip()
        if not content:
            return None

        source_links = data.get("urls") or []
        source_name = data.get("name") or ""
        if not source_name:
            parsed = urlparse(url)
            source_name = parsed.netloc or parsed.path or "unknown"
        description = (data.get("description") or "").strip() or source_name or DESCRIPTION_FALLBACK
        if len(description) > DESCRIPTION_MAX_LENGTH:
            description = description[:DESCRIPTION_MAX_LENGTH] + "..."

        page_type, classify_reason = self._classify_page(url, content, source_name, description)
        logger.info(
            "[opp-pipeline] classify page_type=%s reason=%s url=%s",
            page_type,
            (classify_reason or "")[:160],
            url[:120],
        )

        if page_type == PAGE_TYPE_NOT_SPEAKING:
            logger.info("[opp-pipeline] drop not_speaking url=%s", url[:120])
            return {"source_name": source_name, "description": description, "opportunities": []}

        if page_type == PAGE_TYPE_DIRECT:
            opportunities = self._process_direct_opportunity_page(
                url=url,
                content=content,
                source_links=source_links,
                page_name=source_name,
                page_description=description,
            )
        else:
            opportunities = self._process_blog_or_aggregator(
                source_url=url,
                content=content,
                source_links=source_links,
                page_name=source_name,
                page_description=description,
            )

        logger.info(
            "[opp-pipeline] sync_done returning=%d opportunities url=%s page_type=%s",
            len(opportunities),
            url[:120],
            page_type,
        )
        return {
            "source_name": source_name,
            "description": description,
            "opportunities": opportunities,
        }

    def _classify_page(
        self,
        url: str,
        content: str,
        name: str,
        description: str,
    ) -> Tuple[str, str]:
        host = (urlparse(url).netloc or "").lower()
        if any(h in host for h in _AGGREGATOR_HOST_HINTS):
            return PAGE_TYPE_BLOG, f"host {host} treated as blog_or_aggregator"

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            # Conservative fallback: treat as blog so we require grounded external links
            return PAGE_TYPE_BLOG, "OPENAI_API_KEY missing; defaulting to blog_or_aggregator"

        try:
            client = OpenAI(api_key=api_key)
            model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
            user = (
                f"Page URL: {url}\n"
                f"Page name: {name or '(none)'}\n"
                f"Description: {description or '(none)'}\n\n"
                f"Content:\n---\n{content[:MAX_CLASSIFY_CONTENT]}\n---"
            )
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": CLASSIFY_SYSTEM_PROMPT},
                    {"role": "user", "content": user},
                ],
                temperature=0.1,
            )
            parsed = _llm_json_object(response.choices[0].message.content or "")
            if not parsed:
                return PAGE_TYPE_BLOG, "classify returned invalid JSON; defaulting to blog_or_aggregator"
            page_type = (parsed.get("page_type") or "").strip()
            reason = (parsed.get("reason") or "").strip()
            if page_type not in (PAGE_TYPE_DIRECT, PAGE_TYPE_BLOG, PAGE_TYPE_NOT_SPEAKING):
                return PAGE_TYPE_BLOG, reason or "unknown page_type; defaulting to blog_or_aggregator"
            return page_type, reason
        except Exception as e:
            logger.warning("[opp-pipeline] classify failed url=%s err=%s", url[:120], e)
            return PAGE_TYPE_BLOG, f"classify error: {e}"

    def _resolve_grounded_candidates(
        self,
        source_url: str,
        content: str,
        grounded_urls: List[str],
    ) -> List[Dict[str, str]]:
        grounded = [
            u for u in grounded_urls
            if u and not _urls_same_page(u, source_url)
        ][:80]
        if not grounded:
            logger.info("[opp-pipeline] resolve_urls no grounded external urls source=%s", source_url[:120])
            return []

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            # No LLM: take up to MAX_HOP_URLS external grounded URLs as unnamed candidates
            return [{"event_name": "", "url": u} for u in grounded[:MAX_HOP_URLS]]

        grounded_set = {_normalize_url_key(u): u for u in grounded}
        try:
            client = OpenAI(api_key=api_key)
            model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
            user = (
                f"Source page URL: {source_url}\n\n"
                f"groundedUrls ({len(grounded)}):\n"
                + "\n".join(f"- {u}" for u in grounded[:60])
                + f"\n\nPage content:\n---\n{content[:MAX_RESOLVE_CONTENT]}\n---"
            )
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": RESOLVE_URLS_SYSTEM_PROMPT},
                    {"role": "user", "content": user},
                ],
                temperature=0.1,
            )
            parsed = _llm_json_object(response.choices[0].message.content or "")
            raw_candidates = (parsed or {}).get("candidates") if parsed else None
            if not isinstance(raw_candidates, list):
                return []

            out: List[Dict[str, str]] = []
            seen: Set[Tuple[str, str]] = set()
            for item in raw_candidates:
                if not isinstance(item, dict):
                    continue
                cand_url = (item.get("url") or "").strip()
                if not cand_url:
                    continue
                key = _normalize_url_key(cand_url)
                if key not in grounded_set:
                    # Try exact string match against grounded list
                    if cand_url not in grounded:
                        logger.info(
                            "[opp-pipeline] resolve_urls reject ungrounded url=%s",
                            cand_url[:120],
                        )
                        continue
                    key = _normalize_url_key(cand_url)
                if key in seen:
                    continue
                seen.add(key)
                canonical = grounded_set.get(key, cand_url)
                out.append({
                    "event_name": (item.get("event_name") or "").strip(),
                    "url": canonical,
                })
                if len(out) >= MAX_HOP_URLS:
                    break
            logger.info(
                "[opp-pipeline] resolve_urls source=%s candidates=%d",
                source_url[:120],
                len(out),
            )
            return out
        except Exception as e:
            logger.warning("[opp-pipeline] resolve_urls failed source=%s err=%s", source_url[:120], e)
            return [{"event_name": "", "url": u} for u in grounded[:MAX_HOP_URLS]]

    def _process_direct_opportunity_page(
        self,
        url: str,
        content: str,
        source_links: List[Any],
        page_name: str,
        page_description: str,
    ) -> List[Dict[str, Any]]:
        opportunities, llm_error = self.extractor.extract(content, url=url)
        if llm_error and not opportunities:
            logger.warning("[opp-pipeline] LLM extraction error url=%s err=%s", url[:120], llm_error)

        # Ensure every candidate points at this direct opportunity page when link empty
        for opp in opportunities or []:
            link = (opp.get("link") or opp.get("url") or "").strip()
            if not link or _urls_same_page(link, url):
                opp["link"] = url
            # If extractor pointed elsewhere, keep external only when grounded
            elif not self._url_in_grounded(link, content, source_links, url):
                logger.info(
                    "[opp-pipeline] direct page rewrite ungrounded link=%s -> %s",
                    link[:120],
                    url[:120],
                )
                opp["link"] = url

        if not opportunities:
            # Single opportunity skeleton from this page for verify/refresh to fill
            opportunities = [{
                "link": url,
                "event_name": page_name or "",
                "location": "",
                "topics": [],
                "aipredictedTopics": [],
                "start_date": None,
                "end_date": None,
                "speaking_format": "",
                "delivery_mode": "",
                "target_audiences": [],
                "metadata": {"description": page_description or ""},
                "submissionInfo": {},
            }]

        return self._verify_submit_qualify(
            opportunities,
            source_page_url=url,
            source_page_content=content,
            source_page_links=source_links,
            reject_same_as_source=False,
        )

    def _process_blog_or_aggregator(
        self,
        source_url: str,
        content: str,
        source_links: List[Any],
        page_name: str,
        page_description: str,
    ) -> List[Dict[str, Any]]:
        grounded = _collect_grounded_urls(content, source_links, source_url)
        candidates = self._resolve_grounded_candidates(source_url, content, grounded)
        if not candidates:
            # Fallback: extractor may have produced grounded official links
            extracted, _ = self.extractor.extract(content, url=source_url)
            for opp in extracted or []:
                link = (opp.get("link") or opp.get("url") or "").strip()
                if not link or _urls_same_page(link, source_url):
                    continue
                if not self._url_in_grounded(link, content, source_links, source_url):
                    logger.info(
                        "[opp-pipeline] blog extract drop ungrounded link=%s",
                        link[:120],
                    )
                    continue
                candidates.append({
                    "event_name": (opp.get("event_name") or "").strip(),
                    "url": link,
                })
                if len(candidates) >= MAX_HOP_URLS:
                    break

        if not candidates:
            logger.info("[opp-pipeline] blog no hop candidates source=%s", source_url[:120])
            return []

        # Pre-hop dedupe: skip candidates whose link or (link, event_name) already exists
        hop_urls = [c["url"] for c in candidates[:MAX_HOP_URLS] if c.get("url")]
        known_urls = OpportunityModel.find_urls_already_known_sync(hop_urls)
        existing_keys = OpportunityModel.find_existing_dedupe_keys_sync(
            [{"link": c.get("url"), "event_name": c.get("event_name")} for c in candidates[:MAX_HOP_URLS]]
        )
        filtered_candidates: List[Dict[str, str]] = []
        for cand in candidates[:MAX_HOP_URLS]:
            hop_url = (cand.get("url") or "").strip()
            if not hop_url:
                continue
            if hop_url in known_urls:
                logger.info(
                    "[opp-pipeline] hop_skip_existing_link url=%s from=%s",
                    hop_url[:120],
                    source_url[:120],
                )
                continue
            key = opportunity_dedupe_key(
                {"link": hop_url, "event_name": cand.get("event_name") or ""}
            )
            if key and key in existing_keys:
                logger.info(
                    "[opp-pipeline] hop_skip_existing_dedupe_key url=%s event=%s from=%s",
                    hop_url[:120],
                    (cand.get("event_name") or "")[:80],
                    source_url[:120],
                )
                continue
            filtered_candidates.append(cand)

        if not filtered_candidates:
            logger.info(
                "[opp-pipeline] blog all hop candidates already known source=%s",
                source_url[:120],
            )
            return []

        hop_opportunities: List[Dict[str, Any]] = []
        for cand in filtered_candidates:
            hop_url = cand["url"]
            logger.info(
                "[opp-pipeline] hop url=%s event_name=%s from=%s",
                hop_url[:120],
                (cand.get("event_name") or "")[:80],
                source_url[:120],
            )
            hop_result = self.scraper.scrape(hop_url)
            if not hop_result.get("success"):
                logger.info("[opp-pipeline] hop scrape failed url=%s", hop_url[:120])
                continue
            hop_data = hop_result.get("data") or {}
            hop_content = (hop_data.get("content") or "").strip()
            if not hop_content:
                logger.info("[opp-pipeline] hop empty content url=%s", hop_url[:120])
                continue
            hop_links = hop_data.get("urls") or []
            hop_name = hop_data.get("name") or cand.get("event_name") or ""
            hop_desc = (hop_data.get("description") or "").strip()

            extracted, _ = self.extractor.extract(hop_content, url=hop_url)
            if extracted:
                for opp in extracted:
                    opp["link"] = hop_url
                    if cand.get("event_name") and not (opp.get("event_name") or "").strip():
                        opp["event_name"] = cand["event_name"]
                page_opps = extracted
            else:
                page_opps = [{
                    "link": hop_url,
                    "event_name": cand.get("event_name") or hop_name or "",
                    "location": "",
                    "topics": [],
                    "aipredictedTopics": [],
                    "start_date": None,
                    "end_date": None,
                    "speaking_format": "",
                    "delivery_mode": "",
                    "target_audiences": [],
                    "metadata": {"description": hop_desc or page_description or ""},
                    "submissionInfo": {},
                }]

            verified = self._verify_submit_qualify(
                page_opps,
                source_page_url=hop_url,
                source_page_content=hop_content,
                source_page_links=hop_links,
                reject_same_as_source=False,
                blog_source_url=source_url,
            )
            hop_opportunities.extend(verified)

        return hop_opportunities

    def _url_in_grounded(
        self,
        link: str,
        content: str,
        source_links: List[Any],
        base_url: str,
    ) -> bool:
        grounded = _collect_grounded_urls(content, source_links, base_url)
        link_key = _normalize_url_key(link)
        return any(_normalize_url_key(g) == link_key for g in grounded)

    def _ensure_metrics_from_page(
        self,
        opp: Dict[str, Any],
        *,
        content: str,
        name: str,
        description: str,
    ) -> Dict[str, Any]:
        """Fill missing catalog fields from opportunity page extract (no date guessing)."""
        needs_topics = not (
            (isinstance(opp.get("topics"), list) and opp.get("topics"))
            or (isinstance(opp.get("aipredictedTopics"), list) and opp.get("aipredictedTopics"))
        )
        needs_format = not (opp.get("speaking_format") or "").strip()
        needs_delivery = not (opp.get("delivery_mode") or "").strip()
        needs_audiences = not (
            isinstance(opp.get("target_audiences"), list) and len(opp.get("target_audiences") or []) > 0
        )
        needs_location = not (opp.get("location") or "").strip()
        needs_name = not (opp.get("event_name") or opp.get("title") or "").strip()
        if not (needs_topics or needs_format or needs_delivery or needs_audiences or needs_location or needs_name):
            return opp

        extracted = self.enricher._extract_details_from_page_content(
            content,
            name=name,
            description=description,
        )
        if not extracted:
            return opp
        return self.enricher._overwrite_core_fields_from_page(opp, extracted)

    def _verify_submit_qualify(
        self,
        opportunities: List[Dict[str, Any]],
        *,
        source_page_url: str,
        source_page_content: str,
        source_page_links: List[Any],
        reject_same_as_source: bool,
        blog_source_url: str = "",
    ) -> List[Dict[str, Any]]:
        if not opportunities:
            return []

        # Drop candidates that still point at the original blog/listicle
        if blog_source_url:
            filtered = []
            for opp in opportunities:
                link = (opp.get("link") or opp.get("url") or "").strip()
                if link and _urls_same_page(link, blog_source_url):
                    logger.info(
                        "[opp-pipeline] drop blog-same link before verify link=%s",
                        link[:120],
                    )
                    continue
                filtered.append(opp)
            opportunities = filtered
            if not opportunities:
                return []

        before = len(opportunities)
        opportunities = filter_opportunities_verified_on_official_site(
            opportunities,
            scraper=self.scraper,
            source_page_url=source_page_url,
            source_page_content=source_page_content,
            reject_same_as_source=reject_same_as_source,
        )
        logger.info(
            "[opp-pipeline] after_official_verify kept=%d dropped=%d url=%s",
            len(opportunities),
            before - len(opportunities),
            source_page_url[:120],
        )

        # Extra strict date gate: day precision + future-only (also enforced in verify)
        dated: List[Dict[str, Any]] = []
        for opp in opportunities:
            start_iso = _parse_date_to_iso(opp.get("start_date"), require_day=True)
            end_iso = _parse_date_to_iso(opp.get("end_date"), require_day=True)
            if not start_iso:
                logger.info(
                    "[opp-pipeline] date_reject event=%s link=%s reason=missing_exact_start",
                    (opp.get("event_name") or "")[:80],
                    (opp.get("link") or "")[:120],
                )
                continue
            end_iso = end_iso or start_iso
            if not _is_future_or_today(start_iso) or not _is_future_or_today(end_iso):
                logger.info(
                    "[opp-pipeline] date_reject event=%s link=%s reason=past_event start=%s end=%s",
                    (opp.get("event_name") or "")[:80],
                    (opp.get("link") or "")[:120],
                    start_iso,
                    end_iso,
                )
                continue
            opp["start_date"] = start_iso
            opp["end_date"] = end_iso
            # Fill catalog metrics from opportunity page when verify left gaps
            opp = self._ensure_metrics_from_page(
                opp,
                content=source_page_content,
                name="",
                description="",
            )
            dated.append(opp)
        opportunities = dated

        if not opportunities:
            return []

        opportunities = self.submission_resolver.resolve_opportunities(
            opportunities,
            source_url=source_page_url,
            source_page_content=source_page_content,
            source_page_links=source_page_links,
        )
        qualify_opportunities_batch(
            opportunities,
            scraper=self.scraper,
            source_page_url=source_page_url,
            source_page_content=source_page_content,
        )
        qualified = sum(1 for o in opportunities if o.get("isQualified"))
        logger.info(
            "[opp-pipeline] after_qualify total=%d qualified=%d unqualified=%d url=%s",
            len(opportunities),
            qualified,
            len(opportunities) - qualified,
            source_page_url[:120],
        )
        return opportunities
