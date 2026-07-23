"""
Uses an LLM to extract Speaking Opportunities from scraped website content.
Processes content in chunks with overlap to avoid hallucination and context loss at boundaries.
Topics extracted by the LLM are constrained to the canonical list in speaker_profile_chatbot.TOPICS.
Only future opportunities with start_date/end_date are kept; webinars/seminars (attend-only) are excluded.
"""
import json
import logging
import os
import re
from datetime import datetime, date
from typing import List, Dict, Any, Optional, Tuple
from openai import OpenAI

from app.config.speaker_profile_chatbot import (
    TOPICS as ALLOWED_TOPICS,
    SPEAKING_FORMATS,
    DELIVERY_MODE,
)
from app.helpers.OpportunitySubmissionResolver import (
    normalize_submission_info,
    sync_submission_info_to_metadata,
)
from app.helpers.TargetAudienceCatalog import (
    audience_catalog_maps,
    resolve_target_audiences,
)

logger = logging.getLogger(__name__)

_ALLOWED_TOPICS_SET = set(ALLOWED_TOPICS)
_ALLOWED_TOPICS_LOWER = {t.lower(): t for t in ALLOWED_TOPICS}
_TOPICS_LIST_STR = ", ".join(f'"{t}"' for t in ALLOWED_TOPICS)

_SPEAKING_FORMATS_SET = set(SPEAKING_FORMATS)
_SPEAKING_FORMATS_LOWER = {t.lower(): t for t in SPEAKING_FORMATS}
_SPEAKING_FORMATS_STR = ", ".join(f'"{t}"' for t in SPEAKING_FORMATS)

_DELIVERY_MODE_SET = set(DELIVERY_MODE)
_DELIVERY_MODE_LOWER = {t.lower(): t for t in DELIVERY_MODE}
_DELIVERY_MODE_STR = ", ".join(f'"{t}"' for t in DELIVERY_MODE)


def _filter_single_to_allowed(value: str, allowed: List[str], allowed_lower: dict, default: str = "") -> str:
    """Map a single value to the allowed list (exact or case-insensitive). Returns default if no match."""
    s = (value or "").strip()
    if not s:
        return default
    if s in set(allowed):
        return s
    return allowed_lower.get(s.lower(), default)


def _filter_list_to_allowed(raw_list: List[str], allowed: List[str], allowed_set: set, allowed_lower: dict) -> List[str]:
    """Keep only values in allowed (exact or case-insensitive), deduplicated."""
    if not raw_list:
        return []
    seen = set()
    result = []
    for t in raw_list:
        s = (t or "").strip()
        if not s:
            continue
        if s in allowed_set and s not in seen:
            result.append(s)
            seen.add(s)
            continue
        canonical = allowed_lower.get(s.lower())
        if canonical and canonical not in seen:
            result.append(canonical)
            seen.add(canonical)
    return result


def _filter_topics_to_allowed(raw_topics: List[str]) -> List[str]:
    """Keep only topics that are in ALLOWED_TOPICS (exact or case-insensitive). If none match, return first allowed topic."""
    if not raw_topics:
        return [ALLOWED_TOPICS[0]] if ALLOWED_TOPICS else []
    seen = set()
    result = []
    for t in raw_topics:
        s = (t or "").strip()
        if not s:
            continue
        if s in _ALLOWED_TOPICS_SET and s not in seen:
            result.append(s)
            seen.add(s)
            continue
        canonical = _ALLOWED_TOPICS_LOWER.get(s.lower())
        if canonical and canonical not in seen:
            result.append(canonical)
            seen.add(canonical)
    if not result:
        result = [ALLOWED_TOPICS[0]] if ALLOWED_TOPICS else []
    return result


def _normalize_ai_predicted_topics(raw_topics: Any) -> List[str]:
    """Normalize freeform AI-predicted topics while preserving non-catalog labels."""
    if not isinstance(raw_topics, list):
        return []
    seen = set()
    result = []
    for topic in raw_topics:
        value = str(topic or "").strip()
        if not value:
            continue
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
        if len(result) >= 10:
            break
    return result


def _merge_ai_predicted_topics(*topic_lists: List[str]) -> List[str]:
    seen = set()
    result = []
    for topics in topic_lists:
        for topic in topics or []:
            value = str(topic or "").strip()
            if not value:
                continue
            key = value.lower()
            if key in seen:
                continue
            seen.add(key)
            result.append(value)
            if len(result) >= 10:
                return result
    return result


def _filter_speaking_format(raw: str) -> str:
    """Constrain to SPEAKING_FORMATS; if no match, return first allowed."""
    return _filter_single_to_allowed(
        raw, SPEAKING_FORMATS, _SPEAKING_FORMATS_LOWER,
        default=SPEAKING_FORMATS[0] if SPEAKING_FORMATS else "",
    )


def _filter_delivery_mode(raw: str) -> str:
    """Constrain to DELIVERY_MODE; if no match, return empty string."""
    return _filter_single_to_allowed(raw, DELIVERY_MODE, _DELIVERY_MODE_LOWER, default="")


def _parse_date_to_iso(value: Any, require_day: bool = False) -> Optional[str]:
    """
    Parse a date string from LLM (various formats) to ISO date YYYY-MM-DD.
    Returns None if parsing fails.

    When require_day=True (event start/end), month/year-only values are rejected so
    we never invent day=01 from incomplete dates.
    """
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    # Already ISO-like
    if re.match(r"^\d{4}-\d{2}-\d{2}", s):
        try:
            datetime.strptime(s[:10], "%Y-%m-%d")
            return s[:10]
        except ValueError:
            pass
    day_formats = [
        "%Y-%m-%d",
        "%d %b %Y",
        "%d %B %Y",
        "%b %d, %Y",
        "%B %d, %Y",
        "%m/%d/%Y",
        "%d/%m/%Y",
        "%Y/%m/%d",
    ]
    month_year_formats = [
        "%B %Y",  # March 2025 -> first day of month (only when require_day=False)
        "%b %Y",
    ]
    formats = day_formats if require_day else day_formats + month_year_formats
    for fmt in formats:
        try:
            dt = datetime.strptime(s[:50].strip(), fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _is_future_or_today(iso_date: Optional[str]) -> bool:
    """True if iso_date is today or in the future (date only)."""
    if not iso_date or len(iso_date) < 10:
        return False
    try:
        d = datetime.strptime(iso_date[:10], "%Y-%m-%d").date()
        return d >= date.today()
    except ValueError:
        return False


class SpeakingOpportunityExtractor:
    """Extracts speaking opportunities from markdown content via LLM. Topics are constrained to speaker_profile_chatbot.TOPICS."""

    USER_PROMPT_TEMPLATE = """Extract speaking opportunities from the following website content.

                    Focus only on opportunities where external professionals, experts, or thought leaders can apply or be invited to speak.

                    Look for signals such as:
                    - Call for speakers
                    - Speaker submissions
                    - Submit a talk or proposal
                    - Apply to speak
                    - Become a speaker
                    - Panelist invitations
                    - Workshop leader opportunities
                    - Podcast or media guest invitations
                    - Third-party speaker forms (Typeform, Google Forms, Jotform, Airtable, Microsoft Forms) linked from apply-to-speak CTAs

                    Ignore:
                    - Webinars or seminars meant only for attendees
                    - Past events that are already completed
                    - Events that clearly do not accept external speakers
                    - Meetup.com (and similar) RSVP/attend pages that only list an already-chosen speaker with no apply-to-speak / call-for-speakers path

                    Use only the information present in the provided content. Do not guess or hallucinate missing information.
                    For topics, first use exact values from the allowed topic list. If the opportunity topic is not represented by that list, put the content-based topic labels in aipredictedTopics as a list.
                    Also extract submissionInfo from this chunk: speaker application links, form links (including Typeform/Google Forms/etc.), submission emails, and explicit application deadlines. If an application path is present but no deadline is present, set submissionInfo.deadline to exactly "deadline not found".

                    Website URL:
                    {url}

                    Website Content (Markdown Chunk):
                    {content} 
                    """

    def __init__(
        self,
        chunk_size: int = None,
        chunk_overlap: int = None,
        target_audiences: Optional[List[str]] = None,
    ):
        self.chunk_size = chunk_size or int(os.getenv("LLM_CHUNK_SIZE", "6000"))
        self.chunk_overlap = chunk_overlap or int(os.getenv("LLM_CHUNK_OVERLAP", "1200"))
        (
            self.target_audiences,
            self._target_audiences_set,
            self._target_audiences_lower,
            self._target_audiences_str,
        ) = audience_catalog_maps(target_audiences)

    def _system_prompt(self) -> str:
        return """You are an expert at identifying SPEAKING opportunities for professionals who want to speak at industry events, conferences, podcasts, or expert panels.
                    Only extract opportunities where an external expert has a realistic chance to speak.

                    Valid speaking opportunities include:
                    - Conferences, summits, or forums with call for speakers or speaker submissions
                    - Events that allow proposal submissions (e.g., “submit a talk”, “submit a proposal”, “become a speaker”)
                    - Panel discussions or roundtables where guest experts are invited
                    - Workshops or masterclasses where external professionals are invited to lead sessions
                    - Industry events explicitly inviting speakers or thought leaders
                    - Podcasts, media interviews, or guest expert opportunities
                    - Community or industry meetups that invite external speakers (must have an apply/CFP path — not attend-only RSVP pages)

                    Exclude:
                    - Webinars or seminars where users are only attendees
                    - Events that are strictly attend-only with no speaker participation
                    - Meetup.com RSVP/attend pages that list an already-chosen speaker with no apply-to-speak path
                    - Past events that are already completed
                    - Internal company events not open to external speakers

                    Given a CHUNK of website content (in markdown format), extract only FUTURE speaking opportunities.

                    For each opportunity, return a JSON array of objects with EXACTLY these keys:

                    - link: Official event page or call-for-speakers URL that appears explicitly in the chunk content (markdown href) or is clearly stated as a full URL. Empty string if unknown. NEVER use the scraped Website URL when this page is a blog, listicle, guide, news post, or aggregator listing other events — only use this page's URL when THIS page itself hosts the speaking opportunity / CFS for that event. NEVER invent or guess domains.
                    - event_name: Exact event or opportunity name as written in the chunk. Empty string if not stated. Do NOT invent or paraphrase a new name.
                    - location: Event location (city, country, or "Virtual") if mentioned, otherwise empty string
                    - topics: Array of relevant topics. You MUST choose ONLY from this exact list (use the exact string): """ + _TOPICS_LIST_STR + """. Pick one or more that best match the event. NEVER leave empty - pick at least one from the list.
                    - aipredictedTopics: Array of concise freeform topic labels predicted from the chunk content when no exact topic from the allowed topics list fits the opportunity. Use [] when allowed topics fit well.
                    - start_date: Event start date in ISO format YYYY-MM-DD only when an explicit day is stated (e.g. "2025-03-15", "March 15, 2025"). null if only a month/year is known or the day is missing. Do NOT invent day=01.
                    - end_date: Event end date in ISO format YYYY-MM-DD only when an explicit day is stated. For one-day events use the SAME date as start_date. null if not mentioned with day precision.
                    - speaking_format: You MUST choose exactly ONE from this list (use the exact string): """ + _SPEAKING_FORMATS_STR + """. Pick the one that best matches the opportunity.
                    - delivery_mode: You MUST choose exactly ONE from this list (use the exact string), or empty string if unclear: """ + _DELIVERY_MODE_STR + """
                    - target_audiences: Array of audience types. You MUST choose the closest match(es) ONLY from this exact list (use the exact strings): """ + self._target_audiences_str + """. Always pick at least one closest catalog value when the page implies who the event is for (e.g. developers/engineers → Technical Professionals when present). Do NOT return an empty array when an audience is implied.
                    - application_submission_deadline: Speaker / call-for-speakers application deadline in ISO format YYYY-MM-DD if explicitly stated on the page, otherwise null. Do not guess.
                    - application_submission_closed: boolean, true ONLY if the page explicitly states that applications are closed, the deadline has passed, or submissions are no longer accepted; otherwise false.
                    - submissionInfo: Object that MUST include exactly:
                      - status: "found" if the chunk has a speaker application path, form (including Typeform/Google Forms/Jotform/Airtable/Microsoft Forms), or submission email; "contact_found" if only a general contact email is present and no submission path is present; otherwise "not_found".
                      - applicationLink: direct speaker application / call-for-speakers / apply page URL if present in the chunk, otherwise empty string.
                      - formLink: direct form URL if present in the chunk (Typeform, Google Forms, etc. count), otherwise empty string.
                      - submissionEmail: email specifically for speaker submissions/proposals/applications if present, otherwise empty string.
                      - deadline: application/submission deadline in ISO YYYY-MM-DD if explicitly present; if a submission path is present but deadline is missing, use exactly "deadline not found".
                      - contactEmail: general contact email only when no submission path is present, otherwise empty string.
                      - reason: short evidence-based reason when status is "contact_found" or "not_found", otherwise empty string.
                      - sourceUrl: when formLink/applicationLink is found, set to that submission URL; otherwise the Website URL.
                    - metadata: Object that MUST include:
                    - "description": 3-4 sentences describing the opportunity and why it is a speaking opportunity.
                    - You may also include optional metadata such as contact_email, organizer_name, venue, submission_link, or notes if available.

                    Strict rules:
                    - Return ONLY valid JSON (no explanations or extra text).
                    - Do NOT invent or hallucinate information.
                    - Only extract opportunities explicitly supported by the page content.
                    - Extract submissionInfo only from the provided Website URL and chunk content. Do not infer unseen apply/contact pages here.
                    - Only include events that are in the future.
                    - If no opportunities are found in this chunk, return []."""

    def _chunk_with_overlap(self, text: str, chunk_size: int, overlap: int) -> List[str]:
        """Split text into overlapping chunks to avoid losing context at boundaries."""
        if not text or len(text) <= chunk_size:
            return [text] if text.strip() else []
        chunks = []
        start = 0
        while start < len(text):
            end = start + chunk_size
            chunk = text[start:end]
            chunks.append(chunk)
            start = end - overlap
            if start >= len(text):
                break
        return chunks

    def _parse_llm_json_response(self, text: str) -> List[Dict[str, Any]]:
        """Parse JSON array from LLM response, handling markdown code blocks."""
        text = (text or "").strip()
        if not text:
            return []
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1]) if len(lines) > 2 else text
            if text.startswith("json"):
                text = text[4:].strip()
        try:
            data = json.loads(text)
            return data if isinstance(data, list) else []
        except json.JSONDecodeError:
            match = re.search(r"\[[\s\S]*\]", text)
            if match:
                try:
                    return json.loads(match.group())
                except json.JSONDecodeError:
                    pass
        return []

    def _ensure_topics_non_empty(self, opp: Dict[str, Any]) -> list:
        """Ensure topics is never empty; result is filtered to ALLOWED_TOPICS only."""
        topics = opp.get("topics")
        if isinstance(topics, list) and len(topics) > 0:
            filtered = _filter_topics_to_allowed([str(t).strip() for t in topics if t])
            if filtered:
                return filtered
        event_name = (opp.get("event_name") or opp.get("title") or "").strip()
        speaking_format = (opp.get("speaking_format") or "").strip().lower()
        if speaking_format and speaking_format != "not available":
            return _filter_topics_to_allowed([opp.get("speaking_format", "").strip()])
        if event_name:
            words = [w for w in event_name.replace(",", " ").split() if len(w) > 2][:2]
            if words:
                return _filter_topics_to_allowed(words)
        return _filter_topics_to_allowed([])  # returns first allowed topic as fallback

    def _normalize_opportunity(self, opp: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Normalize LLM output to schema; topics, speaking_format, delivery_mode, target_audiences constrained.
        Parses start_date/end_date from date or start_date/end_date; for one-day events end_date = start_date.
        Returns None if dates are missing or event is in the past (so it gets filtered out).
        """
        raw_topics = opp.get("topics") if isinstance(opp.get("topics"), list) else []
        raw_topic_values = [str(t).strip() for t in raw_topics if str(t or "").strip()]
        allowed_topics = _filter_list_to_allowed(
            raw_topic_values,
            ALLOWED_TOPICS,
            _ALLOWED_TOPICS_SET,
            _ALLOWED_TOPICS_LOWER,
        )
        ai_predicted_topics = _normalize_ai_predicted_topics(
            opp.get("aipredictedTopics") or opp.get("aiPredictedTopics") or opp.get("predictedTopics")
        )
        unmatched_raw_topics = [
            topic for topic in raw_topic_values
            if topic not in _ALLOWED_TOPICS_SET and topic.lower() not in _ALLOWED_TOPICS_LOWER
        ]
        if unmatched_raw_topics:
            ai_predicted_topics = _merge_ai_predicted_topics(ai_predicted_topics, unmatched_raw_topics)
        topics = allowed_topics if allowed_topics else ([] if ai_predicted_topics else self._ensure_topics_non_empty(opp))
        raw_speaking = (opp.get("speaking_format") or "").strip()
        raw_delivery = (opp.get("delivery_mode") or "").strip()
        raw_audiences = opp.get("target_audiences") if isinstance(opp.get("target_audiences"), list) else []

        start_iso = _parse_date_to_iso(opp.get("start_date") or opp.get("date"), require_day=True)
        end_iso = _parse_date_to_iso(opp.get("end_date"), require_day=True)
        if not start_iso:
            return None
        if not end_iso:
            end_iso = start_iso
        if not _is_future_or_today(start_iso):
            return None
        if not _is_future_or_today(end_iso):
            end_iso = start_iso

        meta = opp.get("metadata") if isinstance(opp.get("metadata"), dict) else {}
        meta = dict(meta)
        event_name = opp.get("event_name") or opp.get("title") or ""
        if "description" not in meta or not str(meta.get("description", "")).strip():
            meta["description"] = (meta.get("description") or event_name or "").strip() or ""

        # Application / speaker submission fields (also read from nested metadata keys)
        for mk in ("application_submission_deadline", "speaker_application_deadline"):
            if meta.get(mk):
                p = _parse_date_to_iso(meta.get(mk))
                if p:
                    meta["application_submission_deadline"] = p
                    break
        tl_deadline = opp.get("application_submission_deadline")
        if tl_deadline is not None and str(tl_deadline).strip():
            p = _parse_date_to_iso(tl_deadline)
            if p:
                meta["application_submission_deadline"] = p

        tl_closed = opp.get("application_submission_closed")
        meta_closed = meta.get("application_submission_closed")
        closed = False
        if tl_closed is True:
            closed = True
        elif isinstance(tl_closed, str) and tl_closed.strip().lower() in ("true", "yes", "1"):
            closed = True
        if meta_closed is True:
            closed = True
        elif isinstance(meta_closed, str) and meta_closed.strip().lower() in ("true", "yes", "1"):
            closed = True
        if closed:
            meta["application_submission_closed"] = True

        submission_source = dict(meta)
        if isinstance(opp.get("submissionInfo"), dict):
            submission_source.update(opp.get("submissionInfo") or {})

        normalized = {
            "link": opp.get("link") or opp.get("url") or "",
            "event_name": event_name,
            "location": opp.get("location") or "",
            "topics": topics,
            "aipredictedTopics": ai_predicted_topics,
            "start_date": start_iso,
            "end_date": end_iso,
            "speaking_format": _filter_speaking_format(raw_speaking),
            "delivery_mode": _filter_delivery_mode(raw_delivery),
            "target_audiences": resolve_target_audiences(
                [str(a).strip() for a in raw_audiences if a],
                allowed=self.target_audiences,
                page_snippet=(meta.get("description") or event_name or ""),
                event_name=event_name,
                force_ai_if_empty=True,
            ),
            "submissionInfo": normalize_submission_info(
                submission_source,
                base_url=opp.get("link") or opp.get("url") or "",
            ),
            "metadata": meta,
        }
        sync_submission_info_to_metadata(normalized)
        return normalized

    def _deduplicate_opportunities(self, opportunities: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Merge and deduplicate opportunities by (event_name_normalized, link). Drops past dates and invalid dates (normalize returns None)."""
        seen = set()
        result = []
        for opp in opportunities:
            event_name = (opp.get("event_name") or opp.get("title") or "").strip().lower()[:100]
            link = (opp.get("link") or opp.get("url") or "").strip()
            key = (event_name, link) if event_name or link else json.dumps(opp, sort_keys=True)
            if key not in seen:
                seen.add(key)
                normalized = self._normalize_opportunity(opp)
                if normalized is not None:
                    result.append(normalized)
        return result

    def _extract_from_chunk(
        self,
        client: OpenAI,
        chunk: str,
        chunk_idx: int,
        total_chunks: int,
        model: str,
        url: str = "",
    ) -> List[Dict[str, Any]]:
        """Extract opportunities from a single chunk."""
        if not chunk.strip():
            return []
        logger.debug("LLM extracting from chunk %d/%d (len=%d)", chunk_idx + 1, total_chunks, len(chunk))
        user_prompt = (
            self.USER_PROMPT_TEMPLATE.replace("{url}", url or "").replace("{content}", chunk)
        )
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": self._system_prompt()},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
        )
        text = response.choices[0].message.content
        opps = self._parse_llm_json_response(text) if text else []
        logger.debug("Chunk %d/%d yielded %d opportunities", chunk_idx + 1, total_chunks, len(opps))
        return opps

    def extract(
        self, markdown_content: str, url: str = ""
    ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """
        Process content in overlapping chunks, extract opportunities from each,
        then merge and deduplicate.

        Args:
            markdown_content: Scraped page text (markdown).
            url: Source page URL included in the LLM prompt when available.

        Returns (opportunities, error). error is set if OPENAI_API_KEY missing or LLM fails.
        """
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            logger.error("OPENAI_API_KEY not configured; opportunities could not be extracted")
            return [], "OPENAI_API_KEY not configured; opportunities could not be extracted"

        try:
            client = OpenAI(api_key=api_key)
            content = (markdown_content or "").strip()
            if not content:
                logger.warning("Empty content passed to extract")
                return [], None

            model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
            logger.info("Starting LLM speaking opportunity extraction content_len=%d model=%s", len(content), model)

            chunks = self._chunk_with_overlap(content, self.chunk_size, self.chunk_overlap)
            if not chunks:
                logger.warning("No chunks produced from content")
                return [], None

            logger.info("Processing %d chunks for opportunity extraction", len(chunks))
            all_opportunities: List[Dict[str, Any]] = []
            page_url = (url or "").strip()
            for i, chunk in enumerate(chunks):
                opps = self._extract_from_chunk(
                    client, chunk, i, len(chunks), model, url=page_url
                )
                all_opportunities.extend(opps)

            merged = self._deduplicate_opportunities(all_opportunities)
            logger.info("LLM extraction complete: raw=%d after_dedup=%d", len(all_opportunities), len(merged))
            return merged, None
        except Exception as e:
            logger.exception("Speaking opportunity extraction failed: %s", e)
            return [], str(e)
