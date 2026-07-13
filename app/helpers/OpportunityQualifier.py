"""
Clause-based qualification for extracted opportunities (e.g. application submission open vs closed).

Also provides hard official-site verification: opportunities discovered on blogs/aggregators are
kept only when their official event page confirms a speaking opportunity.

Extend DEFAULT_QUALIFICATION_CLAUSES with more callables as new rules are needed.
Each clause returns None if the opportunity passes that check, or a human-readable failure reason if not.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

from app.helpers.RapidAPIScraper import RapidAPIScraper
from app.helpers.SpeakingOpportunityExtractor import _parse_date_to_iso
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

_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "of",
        "for",
        "to",
        "in",
        "on",
        "at",
        "by",
        "with",
        "from",
        "global",
        "summit",
        "conference",
        "event",
        "annual",
        "virtual",
        "online",
    }
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
    True when official page content shows a speaking opportunity / CFS path.
    Stricter than application-path heuristic: requires explicit speaking phrases (not any email).
    """
    if not text or not str(text).strip():
        return False
    low = str(text).lower()
    return any(phrase in low for phrase in _APPLICATION_SIGNAL_PHRASES)


def _event_name_appears_on_page(event_name: str, content: str) -> bool:
    """
    Require the event to be recognizable on the official page.
    Uses significant tokens from the event name (ignores common stopwords).
    """
    name = (event_name or "").strip()
    if not name:
        return True
    low_content = (content or "").lower()
    if name.lower() in low_content:
        return True

    tokens = [
        t
        for t in re.findall(r"[a-z0-9]+", name.lower())
        if len(t) >= 3 and t not in _STOPWORDS
    ]
    if not tokens:
        # Name was only stopwords / short tokens — fall back to full substring already failed
        return False
    # Require most distinctive tokens (all if few; otherwise at least 2/3)
    need = len(tokens) if len(tokens) <= 2 else max(2, (len(tokens) * 2 + 2) // 3)
    found = sum(1 for t in tokens if t in low_content)
    return found >= need


def _load_official_page_content(
    opp: Dict[str, Any],
    ctx: "OpportunityQualificationContext",
) -> Tuple[Optional[str], Optional[str]]:
    """
    Load content from the opportunity's official event link.
    Reuses source page content when the source URL is the same page.
    Returns (content, error_reason). content is None when loading failed.
    """
    link = (opp.get("link") or opp.get("url") or "").strip()
    if not link:
        return None, "No official event link available to verify this opportunity."

    if _is_pdf_url(link):
        return None, "Official event link points to a PDF; cannot verify speaking opportunity."

    if ctx.source_page_url and ctx.source_page_content and _urls_same_page(link, ctx.source_page_url):
        content = str(ctx.source_page_content).strip() or None
        if not content:
            return None, "Official event page content is empty."
        return content, None

    try:
        result = ctx.scraper.scrape(link)
    except Exception as e:
        logger.warning("Official-site verification scrape failed for %s: %s", link[:120], e)
        return None, "Could not load the official event page to verify this opportunity."

    if not result.get("success"):
        return None, "Could not load the official event page to verify this opportunity."

    content = (result.get("data") or {}).get("content") or ""
    content = str(content).strip() or None
    if not content:
        return None, "Official event page content is empty."
    return content, None


def verify_opportunity_on_official_site(
    opp: Dict[str, Any],
    ctx: "OpportunityQualificationContext",
) -> Tuple[bool, str]:
    """
    Hard check: official event page must confirm a speaking opportunity for this event.
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

    content, load_error = _load_official_page_content(opp, ctx)
    if load_error:
        logger.info(
            "[opp-pipeline] official_verify FAIL event=%s link=%s reason=%s",
            event_name[:80],
            link[:120],
            load_error,
        )
        return False, load_error

    if not _event_name_appears_on_page(event_name, content or ""):
        reason = "Event name was not found on the official event page."
        logger.info(
            "[opp-pipeline] official_verify FAIL event=%s link=%s reason=%s",
            event_name[:80],
            link[:120],
            reason,
        )
        return False, reason

    if not official_page_signals_speaking_opportunity(content or ""):
        reason = (
            "Official event page does not mention a speaking opportunity or call for speakers."
        )
        logger.info(
            "[opp-pipeline] official_verify FAIL event=%s link=%s reason=%s",
            event_name[:80],
            link[:120],
            reason,
        )
        return False, reason

    logger.info(
        "[opp-pipeline] official_verify PASS event=%s link=%s",
        event_name[:80],
        link[:120],
    )
    return True, ""


def filter_opportunities_verified_on_official_site(
    opportunities: List[Dict[str, Any]],
    scraper: RapidAPIScraper,
    source_page_url: str,
    source_page_content: str,
) -> List[Dict[str, Any]]:
    """
    Keep only opportunities whose official event page confirms a speaking opportunity.
    Drops blog/aggregator-only mentions that are not backed by the official site.
    """
    if not opportunities:
        logger.info(
            "[opp-pipeline] official_verify skip: 0 opportunities source=%s",
            (source_page_url or "")[:120],
        )
        return []

    ctx = OpportunityQualificationContext(
        scraper=scraper,
        source_page_url=(source_page_url or "").strip(),
        source_page_content=(source_page_content or "").strip(),
    )
    verified: List[Dict[str, Any]] = []
    for i, opp in enumerate(opportunities, 1):
        ok, reason = verify_opportunity_on_official_site(opp, ctx)
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
