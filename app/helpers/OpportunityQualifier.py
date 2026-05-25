"""
Clause-based qualification for extracted opportunities (e.g. application submission open vs closed).

Extend DEFAULT_QUALIFICATION_CLAUSES with more callables as new rules are needed.
Each clause returns None if the opportunity passes that check, or a human-readable failure reason if not.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Dict, List, Optional, Sequence
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

_APPLICATION_SIGNAL_PHRASES = (
    "apply to speak",
    "speaker application",
    "call for speakers",
    "submit a proposal",
    "become a speaker",
    "speaker submission",
    "propose a talk",
    "submit your talk",
    "speaker interest",
    "speaking opportunity",
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
