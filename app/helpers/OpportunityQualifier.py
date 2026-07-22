"""
Clause-based qualification for extracted opportunities (e.g. application submission open vs closed).

Also provides hard opportunity-URL verification: before save, scrape the opportunity's own link.
If the page is missing, drop. Otherwise ask an LLM whether the page hosts a speaking/CFS
opportunity for this event; if yes, overwrite core fields from that page; if no, drop.

Extend DEFAULT_QUALIFICATION_CLAUSES with more callables as new rules are needed.
Each clause returns None if the opportunity passes that check, or a human-readable failure reason if not.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

from app.agents.EventDetailEnricherAgent import EventDetailEnricherAgent
from app.helpers.RapidAPIScraper import RapidAPIScraper
from app.helpers.SpeakingOpportunityExtractor import _parse_date_to_iso, _is_future_or_today
from app.helpers.OpportunitySubmissionResolver import (
    DEADLINE_NOT_FOUND,
    normalize_submission_info,
    submission_info_has_submission_path,
)

logger = logging.getLogger(__name__)

QualificationClause = Callable[[Dict[str, Any], "OpportunityQualificationContext"], Optional[str]]
"""Returns None if this clause passes; otherwise a short reason for unqualification."""

_EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
    re.IGNORECASE,
)

_SOFT_404_PHRASES = (
    "page not found",
    "404 not found",
    "404 error",
    "this page does not exist",
    "this page doesn't exist",
    "the page you requested",
    "content not found",
    "event not found",
    "no longer available",
    "has been removed",
    "doesn't exist",
    "does not exist",
)

_APPLICATION_SIGNAL_PHRASES = (
    "apply to speak",
    "speaker application",
    "call for speakers",
    "call for proposals",
    "submit a proposal",
    "become a speaker",
    "speaker submission",
    "propose a talk",
    "submit your talk",
    "submit a talk",
    "speaker interest",
    "speaking opportunity",
    "speak at",
    "apply as a speaker",
    "speaker proposals",
    "speakers wanted",
    "cfp",
    "cfs@",  # call for speakers mailbox pattern fragment
    "speakers@",
)


def _is_pdf_url(url: str) -> bool:
    if not url or not isinstance(url, str):
        return False
    path = (urlparse(url.strip()).path or "").rstrip("/")
    return path.lower().endswith(".pdf")


def _normalize_url_key(u: str) -> tuple[str, str]:
    p = urlparse((u or "").strip())
    netloc = (p.netloc or "").lower()
    path = (p.path or "").rstrip("/").lower()
    return netloc, path


def _urls_same_page(a: str, b: str) -> bool:
    return _normalize_url_key(a) == _normalize_url_key(b)


def landing_content_signals_application_path(text: str) -> bool:
    """Heuristic: email, mailto, or common speaker-application phrasing in scraped markdown."""
    if not text or not str(text).strip():
        return False
    raw = str(text)
    if _EMAIL_RE.search(raw):
        return True
    low = raw.lower()
    if "mailto:" in low:
        return True
    return any(phrase in low for phrase in _APPLICATION_SIGNAL_PHRASES)


def official_page_signals_speaking_opportunity(text: str) -> bool:
    """
    Legacy phrase heuristic (kept for callers/tests). Official-site keep/drop now uses LLM
    verification in verify_opportunity_on_official_site instead of this function.
    """
    if not text or not str(text).strip():
        return False
    low = str(text).lower()
    return any(phrase in low for phrase in _APPLICATION_SIGNAL_PHRASES)


def _page_looks_like_missing(content: str) -> bool:
    low = (content or "").lower()
    if not low.strip():
        return True
    if len(low) < 400 and any(p in low for p in _SOFT_404_PHRASES):
        return True
    hits = sum(1 for p in _SOFT_404_PHRASES if p in low)
    return hits >= 2


def _load_official_page_content(
    opp: Dict[str, Any],
    ctx: "OpportunityQualificationContext",
) -> Tuple[Optional[str], Dict[str, str], Optional[str]]:
    """
    Load content from the opportunity's own link (the URL we intend to save).
    Reuses source page content only when that link is the same page.
    Returns (content, page_meta, error_reason). content is None when loading failed.
    page_meta may include name/description from the scraper.
    """
    link = (opp.get("link") or opp.get("url") or "").strip()
    if not link:
        return None, {}, "No opportunity URL available to verify this opportunity."

    if _is_pdf_url(link):
        return None, {}, "Opportunity URL points to a PDF; cannot verify speaking opportunity."

    if ctx.source_page_url and ctx.source_page_content and _urls_same_page(link, ctx.source_page_url):
        content = str(ctx.source_page_content).strip() or None
        if not content:
            return None, {}, "Opportunity URL page content is empty."
        return content, {"name": "", "description": ""}, None

    try:
        result = ctx.scraper.scrape(link)
    except Exception as e:
        logger.warning("Opportunity URL verification scrape failed for %s: %s", link[:120], e)
        return None, {}, "Could not load the opportunity URL to verify this opportunity."

    if not result.get("success"):
        return None, {}, "Could not load the opportunity URL to verify this opportunity."

    data = result.get("data") or {}
    content = (data.get("content") or "")
    content = str(content).strip() or None
    if not content:
        return None, {}, "Opportunity URL page content is empty."
    page_meta = {
        "name": str(data.get("name") or "").strip(),
        "description": str(data.get("description") or "").strip(),
    }
    return content, page_meta, None


def verify_opportunity_on_official_site(
    opp: Dict[str, Any],
    ctx: "OpportunityQualificationContext",
    *,
    reject_same_as_source: bool = False,
) -> Tuple[bool, str]:
    """
    Hard check before save: scrape the opportunity URL, then ask an LLM whether that page
    hosts a speaking/CFS opportunity for this event.
    Drop if missing page or LLM says no. If yes, overwrite core fields from the same LLM response.
    Returns (ok, reason). reason is empty when ok.
    """
    event_name = (opp.get("event_name") or opp.get("title") or "").strip()
    link = (opp.get("link") or opp.get("url") or "").strip()
    logger.debug(
        "[opp-pipeline] official_verify start event=%s link=%s source=%s",
        event_name[:80],
        link[:120],
        (ctx.source_page_url or "")[:120],
    )

    if reject_same_as_source and ctx.source_page_url and link and _urls_same_page(link, ctx.source_page_url):
        reason = (
            "Opportunity link still points at the blog/aggregator source page; "
            "official event URL was not resolved."
        )
        logger.info(
            "[opp-pipeline] official_verify FAIL event=%s link=%s reason=%s",
            event_name[:80],
            link[:120],
            reason,
        )
        return False, reason

    content, page_meta, load_error = _load_official_page_content(opp, ctx)
    if load_error:
        logger.info(
            "[opp-pipeline] official_verify FAIL event=%s link=%s reason=%s",
            event_name[:80],
            link[:120],
            load_error,
        )
        return False, load_error

    if _page_looks_like_missing(content or ""):
        reason = "Opportunity URL page looks missing or unavailable."
        logger.info(
            "[opp-pipeline] official_verify FAIL event=%s link=%s reason=%s",
            event_name[:80],
            link[:120],
            reason,
        )
        return False, reason

    enricher = ctx.enricher or EventDetailEnricherAgent(rapidapi_scraper=ctx.scraper)
    ok, reason, updated = enricher.verify_and_refresh_from_page_content(
        opp,
        content or "",
        name=page_meta.get("name") or "",
        description=page_meta.get("description") or "",
    )
    if not ok:
        logger.info(
            "[opp-pipeline] official_verify FAIL event=%s link=%s reason=%s",
            event_name[:80],
            link[:120],
            reason,
        )
        return False, reason

    # Mutate original dict in place so callers keep the same object reference
    opp.clear()
    opp.update(updated)

    # Strict event dates: day precision required. If CFS page lacks dates, try site homepage.
    start_iso = _parse_date_to_iso(opp.get("start_date"), require_day=True)
    end_iso = _parse_date_to_iso(opp.get("end_date"), require_day=True)
    if not start_iso:
        start_iso, end_iso = _try_exact_dates_from_site_home(opp, ctx)
    if not start_iso:
        reason = "Exact event start date (day precision) not found on opportunity page."
        logger.info(
            "[opp-pipeline] official_verify FAIL event=%s link=%s reason=%s",
            event_name[:80],
            link[:120],
            reason,
        )
        return False, reason
    end_iso = end_iso or start_iso
    if not _is_future_or_today(start_iso) or not _is_future_or_today(end_iso):
        reason = (
            f"Event start/end date is in the past (start={start_iso}, end={end_iso})."
        )
        logger.info(
            "[opp-pipeline] official_verify FAIL event=%s link=%s reason=past_date %s",
            event_name[:80],
            link[:120],
            reason,
        )
        return False, reason
    opp["start_date"] = start_iso
    opp["end_date"] = end_iso

    logger.info(
        "[opp-pipeline] official_verify PASS event=%s link=%s updated_name=%s location=%s dates=%s..%s",
        event_name[:80],
        link[:120],
        (opp.get("event_name") or "")[:80],
        (opp.get("location") or "")[:80],
        str(opp.get("start_date") or "")[:10],
        str(opp.get("end_date") or "")[:10],
    )
    return True, ""


def _try_exact_dates_from_site_home(
    opp: Dict[str, Any],
    ctx: "OpportunityQualificationContext",
) -> Tuple[Optional[str], Optional[str]]:
    """When the opportunity/CFS page has no day-precision dates, try the site origin homepage."""
    link = (opp.get("link") or opp.get("url") or "").strip()
    if not link:
        return None, None
    parsed = urlparse(link)
    if not parsed.scheme or not parsed.netloc:
        return None, None
    home = f"{parsed.scheme}://{parsed.netloc}/"
    if _urls_same_page(home, link):
        return None, None
    try:
        result = ctx.scraper.scrape(home)
    except Exception as e:
        logger.info("[opp-pipeline] date_home scrape failed home=%s err=%s", home[:120], e)
        return None, None
    if not result.get("success"):
        return None, None
    data = result.get("data") or {}
    content = (data.get("content") or "").strip()
    if not content:
        return None, None
    enricher = ctx.enricher or EventDetailEnricherAgent(rapidapi_scraper=ctx.scraper)
    start_iso, end_iso = enricher.extract_exact_dates_from_content(
        content,
        name=data.get("name") or "",
        description=data.get("description") or "",
    )
    if start_iso:
        logger.info(
            "[opp-pipeline] date_home filled start=%s end=%s home=%s for link=%s",
            start_iso,
            end_iso or start_iso,
            home[:120],
            link[:120],
        )
    return start_iso, end_iso


def filter_opportunities_verified_on_official_site(
    opportunities: List[Dict[str, Any]],
    scraper: RapidAPIScraper,
    source_page_url: str,
    source_page_content: str,
    *,
    reject_same_as_source: bool = False,
) -> List[Dict[str, Any]]:
    """
    For each opportunity, scrape its URL and LLM-verify speaking/CFS presence.
    Drop when the page is dead or LLM says it is not a speaking opportunity for this event.
    For kept opportunities, overwrite event_name/location/dates/speaking fields from that URL.
    When reject_same_as_source is True, drop opportunities whose link is still the blog/source page.
    """
    if not opportunities:
        logger.info(
            "[opp-pipeline] official_verify skip: 0 opportunities source=%s",
            (source_page_url or "")[:120],
        )
        return []

    enricher = EventDetailEnricherAgent(rapidapi_scraper=scraper)
    ctx = OpportunityQualificationContext(
        scraper=scraper,
        source_page_url=(source_page_url or "").strip(),
        source_page_content=(source_page_content or "").strip(),
        enricher=enricher,
    )
    verified: List[Dict[str, Any]] = []
    for i, opp in enumerate(opportunities, 1):
        ok, reason = verify_opportunity_on_official_site(
            opp, ctx, reject_same_as_source=reject_same_as_source
        )
        event_name = (opp.get("event_name") or opp.get("title") or "")[:80]
        link = (opp.get("link") or opp.get("url") or "")[:120]
        if ok:
            opp["verifiedOnOfficialSite"] = True
            opp["officialVerificationFailureReason"] = None
            verified.append(opp)
            logger.info(
                "[opp-pipeline] official_verify [%d/%d] KEEP event=%s link=%s",
                i,
                len(opportunities),
                event_name,
                link,
            )
        else:
            opp["verifiedOnOfficialSite"] = False
            opp["officialVerificationFailureReason"] = reason
            logger.info(
                "[opp-pipeline] official_verify [%d/%d] DROP event=%s link=%s reason=%s",
                i,
                len(opportunities),
                event_name,
                link,
                reason,
            )
    logger.info(
        "[opp-pipeline] official_verify summary kept=%d/%d source=%s",
        len(verified),
        len(opportunities),
        (source_page_url or "")[:120],
    )
    return verified


def _get_meta(opp: Dict[str, Any]) -> Dict[str, Any]:
    m = opp.get("metadata")
    return m if isinstance(m, dict) else {}


def _meta_bool_true(meta: Dict[str, Any], key: str) -> bool:
    v = meta.get(key)
    if v is True:
        return True
    if isinstance(v, str) and v.strip().lower() in ("true", "yes", "1", "closed"):
        return True
    return False


def _application_deadline_iso(opp: Dict[str, Any]) -> Optional[str]:
    raw_submission_info = opp.get("submissionInfo")
    if isinstance(raw_submission_info, dict):
        submission_info = normalize_submission_info(
            raw_submission_info,
            base_url=(opp.get("link") or opp.get("url") or ""),
        )
        deadline = submission_info.get("deadline")
        if deadline and deadline != DEADLINE_NOT_FOUND:
            parsed = _parse_date_to_iso(deadline)
            if parsed:
                return parsed

    meta = _get_meta(opp)
    for key in ("application_submission_deadline", "speaker_application_deadline"):
        raw = meta.get(key)
        if raw is None or raw == "":
            continue
        parsed = _parse_date_to_iso(raw)
        if parsed:
            return parsed
    return None


def clause_application_submission(
    opp: Dict[str, Any],
    ctx: "OpportunityQualificationContext",
) -> Optional[str]:
    """
    Fail if applications are explicitly closed or deadline is in the past.
    If deadline is unknown, scrape the opportunity link (or reuse source page content) and look for
    contact/application signals; fail if none found.
    """
    meta = _get_meta(opp)

    if _meta_bool_true(meta, "application_submission_closed"):
        return "Application submission is closed according to the source content."

    raw_submission_info = opp.get("submissionInfo")
    if isinstance(raw_submission_info, dict):
        submission_info = normalize_submission_info(
            raw_submission_info,
            base_url=(opp.get("link") or opp.get("url") or ""),
        )
        status = submission_info.get("status")
        if status == "contact_found":
            return submission_info.get("reason") or "Submission details not found; contact email found."
        if status == "not_found":
            return submission_info.get("reason") or "Speaker submission details not found."

    deadline_iso = _application_deadline_iso(opp)
    if deadline_iso:
        try:
            d = date.fromisoformat(deadline_iso[:10])
        except ValueError:
            d = None
        if d is not None and d < date.today():
            return f"Application submission deadline ({deadline_iso}) has passed."

        # Known deadline still in the future (or today): qualified for this clause
        return None

    if isinstance(raw_submission_info, dict) and submission_info_has_submission_path(raw_submission_info):
        return None

    # No parsed deadline: try speaker/event landing page
    link = (opp.get("link") or opp.get("url") or "").strip()
    if not link:
        return "No event link available to verify speaker application information."

    if _is_pdf_url(link):
        return "Event link points to a PDF; cannot verify speaker application information."

    content: Optional[str] = None
    if ctx.source_page_url and ctx.source_page_content and _urls_same_page(link, ctx.source_page_url):
        content = ctx.source_page_content
    else:
        try:
            result = ctx.scraper.scrape(link)
        except Exception as e:
            logger.warning("Qualification scrape failed for %s: %s", link[:80], e)
            return "Could not load the event/speaker page to verify application information."

        if not result.get("success"):
            return "Could not load the event/speaker page to verify application information."

        content = (result.get("data") or {}).get("content") or ""
        content = str(content).strip() or None

    if content and landing_content_signals_application_path(content):
        return None

    return (
        "Speaker application deadline not found in extracted data, and no clear application "
        "contact or submission path was found on the event page."
    )


DEFAULT_QUALIFICATION_CLAUSES: Sequence[QualificationClause] = (clause_application_submission,)


@dataclass
class OpportunityQualificationContext:
    scraper: RapidAPIScraper
    source_page_url: str = ""
    source_page_content: str = ""
    enricher: Optional[EventDetailEnricherAgent] = field(default=None)


def run_qualification(
    opportunity: Dict[str, Any],
    ctx: OpportunityQualificationContext,
    clauses: Sequence[QualificationClause] = DEFAULT_QUALIFICATION_CLAUSES,
) -> tuple[bool, str]:
    """
    Run clauses in order. First non-None reason fails qualification.
    Returns (is_qualified, reason_for_unqualify). reason is empty when qualified.
    """
    for clause in clauses:
        reason = clause(opportunity, ctx)
        if reason:
            return False, reason.strip()
    return True, ""


def qualify_opportunities_batch(
    opportunities: List[Dict[str, Any]],
    scraper: RapidAPIScraper,
    source_page_url: str,
    source_page_content: str,
    clauses: Sequence[QualificationClause] = DEFAULT_QUALIFICATION_CLAUSES,
) -> None:
    """Mutates each opportunity with isQualified (bool) and reasonForUnqualify (str or None)."""
    ctx = OpportunityQualificationContext(
        scraper=scraper,
        source_page_url=(source_page_url or "").strip(),
        source_page_content=(source_page_content or "").strip(),
    )
    for opp in opportunities:
        ok, reason = run_qualification(opp, ctx, clauses=clauses)
        opp["isQualified"] = ok
        opp["reasonForUnqualify"] = None if ok else reason
