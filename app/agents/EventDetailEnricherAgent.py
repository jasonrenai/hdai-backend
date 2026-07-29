"""
Agent that enriches opportunities with incomplete details by scraping event URLs
via RapidAPI and extracting missing fields (location, topics, start_date, end_date,
speaking_format, delivery_mode, target_audiences, metadata) using an LLM.
Topics are constrained to the canonical list in speaker_profile_chatbot.TOPICS.
"""
import json
import os
import re
from typing import Dict, Any, Optional, List

from openai import OpenAI

from app.helpers.RapidAPIScraper import RapidAPIScraper
from app.config.speaker_profile_chatbot import (
    TOPICS as ALLOWED_TOPICS,
    SPEAKING_FORMATS,
    DELIVERY_MODE,
)
from app.helpers.TargetAudienceCatalog import (
    audience_catalog_maps,
    resolve_target_audiences,
)

_ALLOWED_TOPICS_SET = set(ALLOWED_TOPICS)
_ALLOWED_TOPICS_LOWER = {t.lower(): t for t in ALLOWED_TOPICS}
_TOPICS_LIST_STR = ", ".join(f'"{t}"' for t in ALLOWED_TOPICS)

_SPEAKING_FORMATS_LOWER = {t.lower(): t for t in SPEAKING_FORMATS}
_SPEAKING_FORMATS_STR = ", ".join(f'"{t}"' for t in SPEAKING_FORMATS)
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


def _filter_speaking_format(raw: str) -> str:
    return _filter_single_to_allowed(
        raw, SPEAKING_FORMATS, _SPEAKING_FORMATS_LOWER,
        default=SPEAKING_FORMATS[0] if SPEAKING_FORMATS else "",
    )


def _filter_delivery_mode(raw: str) -> str:
    return _filter_single_to_allowed(raw, DELIVERY_MODE, _DELIVERY_MODE_LOWER, default="")


def _filter_topics_to_allowed(raw_topics: List[str]) -> List[str]:
    """Keep only topics in ALLOWED_TOPICS (exact or case-insensitive). If none match, return first allowed topic."""
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


class EventDetailEnricherAgent:
    """
    Agent that enriches opportunities with incomplete details by scraping
    each event URL via RapidAPI and extracting missing fields via LLM.
    Topics are constrained to speaker_profile_chatbot.TOPICS.
    Target audiences come from the provided catalog (Mongo speakerTargetAudeince names).
    """

    def __init__(
        self,
        rapidapi_scraper: RapidAPIScraper = None,
        target_audiences: Optional[List[str]] = None,
    ):
        self.rapidapi_scraper = rapidapi_scraper or RapidAPIScraper()
        (
            self.target_audiences,
            self._target_audiences_set,
            self._target_audiences_lower,
            self._target_audiences_str,
        ) = audience_catalog_maps(target_audiences)

    def _enricher_system_prompt(self) -> str:
        return """You are an expert at extracting event details from webpage content.
Given scraped content from an event page (markdown format), extract structured event information.

The content may include: event name, description, venue, location, date/time, topics, format, delivery mode, etc.

Extract and return a JSON object with EXACTLY these keys (no array, single object):
- event_name: Full name of the event (from page title/heading if not in content)
- location: City, country, or "Virtual" (e.g. "Leipzig, Germany", "New York, USA")
- topics: Array of relevant topics. You MUST choose ONLY from this exact list (use the exact string): """ + _TOPICS_LIST_STR + """. Pick one or more that best match the event. NEVER leave empty - pick at least one from the list.
- start_date: Event start date in ISO format YYYY-MM-DD (e.g. "2026-03-06") only when an explicit calendar day is present. null if only month/year is known. Do NOT invent day=01.
- end_date: Event end date in ISO format YYYY-MM-DD. For one-day events use the SAME date as start_date. null if day precision is missing.
- speaking_format: You MUST choose exactly ONE from this list (use the exact string): """ + _SPEAKING_FORMATS_STR + """
- delivery_mode: You MUST choose exactly ONE from this list (use the exact string), or empty string if unclear: """ + _DELIVERY_MODE_STR + """
- target_audiences: Array of audience types. You MUST choose the closest match(es) ONLY from this exact list (use the exact strings): """ + self._target_audiences_str + """. Always pick at least one closest catalog value when the page implies who the event is for. Do NOT return an empty array when an audience is implied.
- metadata: Object with description (1-2 sentences), venue name if mentioned, contact info if any. Include when present on the page: application_submission_deadline (ISO YYYY-MM-DD or omit), application_submission_closed (boolean, true only if explicitly closed / no longer accepting). Use {} for empty.

Return ONLY valid JSON, no other text. Extract only what is explicitly present; use empty string, [], or null for missing fields. topics must always have at least one item from the allowed list."""

    def _enricher_user_prompt_template(self) -> str:
        return """Extract event details from this scraped page content.

Page name/title from scraper: {name}

Description snippet: {description}

Full content:
---
{content}
---

Return a single JSON object with keys: event_name, location, topics, start_date, end_date, speaking_format, delivery_mode, target_audiences, metadata. Use start_date and end_date in ISO format (YYYY-MM-DD) only when an explicit calendar day is present; use null if only month/year is known. For one-day events set end_date equal to start_date. Prefer empty/null over guessing. Use ONLY: topics from """ + _TOPICS_LIST_STR + """; speaking_format from """ + _SPEAKING_FORMATS_STR + """; delivery_mode from """ + _DELIVERY_MODE_STR + """; target_audiences from """ + self._target_audiences_str + """."""

    def _verify_and_refresh_system_prompt(self) -> str:
        return """You verify whether a scraped webpage hosts a real OPEN speaking opportunity for a specific event, then extract updated event details from that page.

STRICT RULE — hosts_speaking_opportunity=true ONLY when BOTH are true:
1) An external professional (not already selected) can APPLY TO SPEAK as a speaker on THIS page: call for speakers, apply to speak, speaker application, speaker signup, invite to speak, become a speaker, panelist application, or workshop facilitator application where the person is invited as a SPEAKER.
2) The page is an industry/professional speaker path (not academic research, not a generic presentation/abstract proposal track).

A contact email, RSVP, tickets, agenda, "meet our speakers", or "ways to participate" alone is NEVER enough for true.

ALWAYS set hosts_speaking_opportunity=false for these (even if an application form exists) — they are NOT speaking opportunities for our product:
- Call for Abstract / Call for Abstracts (industry or academic)
- Opportunity for Presentation / apply to present / present here / presentation opportunity
- Presentation Proposal / call for proposals aimed at presentations (including URLs or titles like call-for-proposals, /present/, propselect presentation proposals)
- Call for Papers / paper submission / camera-ready / peer-reviewed manuscripts
- Session abstract portals, Cvent abstract systems, and similar abstract-submission flows

Decide hosts_speaking_opportunity=false also when ANY of these apply (judge from overall page meaning, not isolated keywords):
- Attend-only / RSVP / register / buy tickets / agenda / schedule / event homepage / org homepage with no open apply-to-speak path
- Meetup.com (or similar) group home or event RSVP pages without an explicit apply-to-speak / call-for-speakers path
- Eventbrite (or similar) ticket/registration pages with no open speaker application
- LinkedIn company pages, social posts, or link shorteners that promote an event but do not host an apply-to-speak path
- "Meet our speakers" / featured / invited / curated speaker lists with no open application
- Sponsorship, exhibitor booths, partner packages, advertising, or "ways to participate" hubs that are mainly sponsor/exhibitor/partner tracks
- Exhibition / trade-show registration that is not an open apply-to-speak path
- The page is unrelated to the claimed event, or speaking is not evidenced on THIS page
- Only a general info@ / contact email with no speaker submission path

Keep true ONLY for clear industry/professional OPEN speaker paths such as: call for speakers, apply to speak, speaker application forms, become a speaker — where the person applies to be a SPEAKER (not merely to submit a presentation, abstract, or paper). If the page is mainly "present" / "presentation proposal" / "call for abstracts", return false even if it sounds related to speaking.

Return ONLY valid JSON (no markdown) with EXACTLY these keys:
- hosts_speaking_opportunity: boolean
- reason: short evidence-based explanation (cite what open apply-to-speak path exists, or why it fails)
- event_name: Full event name from the page when present, else empty string
- location: City/country or "Virtual" when present, else empty string
- topics: Array chosen ONLY from this list (exact strings): """ + _TOPICS_LIST_STR + """. Empty array if unclear.
- start_date: ISO YYYY-MM-DD when an explicit day is present, else null. Never invent day=01 from month/year only.
- end_date: ISO YYYY-MM-DD when an explicit day is present, else null (same as start_date for one-day events)
- speaking_format: Exactly ONE from """ + _SPEAKING_FORMATS_STR + """ when clear, else empty string
- delivery_mode: Exactly ONE from """ + _DELIVERY_MODE_STR + """ when clear, else empty string
- target_audiences: Array of closest matches ONLY from """ + self._target_audiences_str + """. Always pick at least one when the page implies an audience; do not return empty when audience is implied.
- metadata: Object with optional description and other page facts, or {}

Do not invent facts. Prefer empty/null over guessing. When unsure, return hosts_speaking_opportunity=false."""

    VERIFY_AND_REFRESH_USER_PROMPT_TEMPLATE = """Candidate opportunity discovered from another source page:
- Claimed event_name: {event_name}
- Claimed location: {location}
- Claimed start_date: {start_date}
- Claimed end_date: {end_date}
- Opportunity URL: {link}

Page name/title from scraper: {name}
Description snippet: {description}

Scraped opportunity URL content:
---
{content}
---

Decide hosts_speaking_opportunity using the system rules.
Set true ONLY for a clear OPEN apply-to-speak / call-for-speakers path (person applies to be a SPEAKER).
ALWAYS set false for: Call for Abstract(s), Opportunity for Presentation / apply to present / present pages, Presentation Proposal / call-for-proposals for presentations, Call for Papers, abstract portals, Meetup/Eventbrite attend-RSVP-ticket pages, LinkedIn/social promos, agenda/homepages without apply-to-speak, sponsor/exhibitor hubs, featured-speaker-only pages, or contact-email-only pages.
If true, extract/update event details from THIS page content.
Return only the JSON object described in the system prompt."""

    def _is_opportunity_incomplete(self, opp: Dict[str, Any]) -> bool:
        """Return True if opportunity needs enrichment (missing key details)."""
        has_location = bool((opp.get("location") or "").strip())
        has_topics = bool(opp.get("topics") and len(opp.get("topics", [])) > 0)
        has_start_date = opp.get("start_date") is not None and str(opp.get("start_date")).strip() != ""
        has_end_date = opp.get("end_date") is not None and str(opp.get("end_date")).strip() != ""
        has_date = has_start_date and has_end_date
        has_speaking_format = bool((opp.get("speaking_format") or "").strip()) and (opp.get("speaking_format") or "").lower() != "not available"
        has_delivery_mode = bool((opp.get("delivery_mode") or "").strip())
        has_target_audiences = bool(opp.get("target_audiences") and len(opp.get("target_audiences", [])) > 0)
        missing = sum([
            not has_location,
            not has_topics,
            not has_date,
            not has_speaking_format,
            not has_delivery_mode,
            not has_target_audiences,
        ])
        return missing >= 2

    def _parse_llm_json_object(self, text: str) -> Optional[Dict[str, Any]]:
        """Parse JSON object from LLM response."""
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
                    return json.loads(match.group())
                except json.JSONDecodeError:
                    pass
        return None

    def _ensure_topics_non_empty(self, opp: Dict[str, Any]) -> list:
        """Ensure topics is never empty; result is filtered to ALLOWED_TOPICS only."""
        topics = opp.get("topics")
        if isinstance(topics, list) and len(topics) > 0:
            filtered = _filter_topics_to_allowed([str(t).strip() for t in topics if t])
            if filtered:
                return filtered
        event_name = (opp.get("event_name") or "").strip()
        speaking_format = (opp.get("speaking_format") or "").strip().lower()
        if speaking_format and speaking_format != "not available":
            return _filter_topics_to_allowed([(opp.get("speaking_format") or "").strip()])
        if event_name:
            words = [w for w in event_name.replace(",", " ").split() if len(w) > 2][:2]
            if words:
                return _filter_topics_to_allowed(words)
        return _filter_topics_to_allowed([])

    def _merge_enriched(self, original: Dict[str, Any], enriched: Dict[str, Any]) -> Dict[str, Any]:
        """Merge enriched fields into original, only filling in empty/missing values."""
        result = dict(original)
        result["link"] = original.get("link") or original.get("url") or ""

        def _fill(key: str, default_empty=None):
            if default_empty is None:
                default_empty = ""
            orig_val = result.get(key, default_empty)
            new_val = enriched.get(key)
            if orig_val is None or orig_val == "" or orig_val == [] or orig_val == {}:
                if new_val is not None:
                    result[key] = new_val
            elif key == "event_name" and not (orig_val and str(orig_val).strip()):
                if new_val:
                    result[key] = new_val

        _fill("event_name")
        _fill("location")
        _fill("topics", [])
        _fill("start_date")
        _fill("end_date")
        if not result.get("start_date") and enriched.get("date"):
            result["start_date"] = enriched.get("date")
        if not result.get("end_date") and result.get("start_date"):
            result["end_date"] = result["start_date"]
        _fill("speaking_format")
        _fill("delivery_mode")
        _fill("target_audiences")
        if enriched.get("metadata") and isinstance(enriched["metadata"], dict):
            meta = result.get("metadata") or {}
            if not isinstance(meta, dict):
                meta = {}
            meta.update(enriched["metadata"])
            result["metadata"] = meta
        return result

    @staticmethod
    def _is_virtual_location(location: str) -> bool:
        tokens = set(re.findall(r"[a-z0-9]+", (location or "").lower()))
        return bool(tokens & {"virtual", "online", "remote", "webinar"})

    def _overwrite_core_fields_from_page(
        self,
        original: Dict[str, Any],
        extracted: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Overwrite core opportunity fields from the opportunity URL page extract.
        Updates: event_name, location (skip overwrite when page location is only virtual/online
        and original already has a concrete location), start_date, end_date, speaking_format,
        delivery_mode when the page provides values.
        """
        from app.helpers.SpeakingOpportunityExtractor import _parse_date_to_iso

        result = dict(original)
        result["link"] = original.get("link") or original.get("url") or ""

        event_name = (extracted.get("event_name") or "").strip()
        if event_name:
            result["event_name"] = event_name

        page_location = (extracted.get("location") or "").strip()
        if page_location:
            orig_location = (result.get("location") or "").strip()
            if self._is_virtual_location(page_location) and orig_location and not self._is_virtual_location(orig_location):
                # Keep concrete location from discovery when page only says Virtual/Online
                pass
            else:
                result["location"] = page_location

        start_raw = extracted.get("start_date") or extracted.get("date")
        start_iso = (
            _parse_date_to_iso(str(start_raw).strip(), require_day=True)
            if start_raw not in (None, "")
            else None
        )
        if start_iso:
            result["start_date"] = start_iso

        end_raw = extracted.get("end_date")
        end_iso = (
            _parse_date_to_iso(str(end_raw).strip(), require_day=True)
            if end_raw not in (None, "")
            else None
        )
        if end_iso:
            result["end_date"] = end_iso
        elif result.get("start_date") and not (result.get("end_date") or "").strip():
            result["end_date"] = result["start_date"]

        speaking_format = _filter_speaking_format((extracted.get("speaking_format") or "").strip())
        if speaking_format:
            result["speaking_format"] = speaking_format

        delivery_mode = _filter_delivery_mode((extracted.get("delivery_mode") or "").strip())
        if delivery_mode:
            result["delivery_mode"] = delivery_mode

        raw_topics = extracted.get("topics")
        if isinstance(raw_topics, list) and raw_topics:
            filtered_topics = _filter_topics_to_allowed([str(t).strip() for t in raw_topics if t])
            if filtered_topics:
                result["topics"] = filtered_topics

        raw_audiences = extracted.get("target_audiences")
        if isinstance(raw_audiences, list):
            meta_desc = ""
            if isinstance(extracted.get("metadata"), dict):
                meta_desc = str((extracted.get("metadata") or {}).get("description") or "")
            result["target_audiences"] = resolve_target_audiences(
                [str(a).strip() for a in raw_audiences if a],
                allowed=self.target_audiences,
                page_snippet=meta_desc,
                event_name=(extracted.get("event_name") or result.get("event_name") or ""),
                force_ai_if_empty=True,
            )
        elif not (result.get("target_audiences") or []):
            result["target_audiences"] = resolve_target_audiences(
                [],
                allowed=self.target_audiences,
                page_snippet=str((result.get("metadata") or {}).get("description") or "")
                if isinstance(result.get("metadata"), dict)
                else "",
                event_name=(result.get("event_name") or ""),
                force_ai_if_empty=True,
            )

        if extracted.get("metadata") and isinstance(extracted["metadata"], dict):
            meta = result.get("metadata") or {}
            if not isinstance(meta, dict):
                meta = {}
            meta.update(extracted["metadata"])
            result["metadata"] = meta

        return result

    def _extract_details_from_page_content(
        self,
        content: str,
        *,
        name: str = "",
        description: str = "",
    ) -> Optional[Dict[str, Any]]:
        """LLM-extract structured event details from already-scraped page content."""
        content = (content or "").strip()
        if not content:
            return None
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return None
        try:
            client = OpenAI(api_key=api_key)
            model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": self._enricher_system_prompt()},
                    {
                        "role": "user",
                        "content": self._enricher_user_prompt_template().format(
                            name=name or "(not provided)",
                            description=description or "(not provided)",
                            content=content[:8000],
                        ),
                    },
                ],
                temperature=0.1,
            )
            text = response.choices[0].message.content
            return self._parse_llm_json_object(text) if text else None
        except Exception:
            return None

    def refresh_core_fields_from_page_content(
        self,
        opp: Dict[str, Any],
        content: str,
        *,
        name: str = "",
        description: str = "",
    ) -> Dict[str, Any]:
        """
        Re-extract core fields from the opportunity URL page and overwrite them on the opportunity.
        Used after confirming the page hosts a speaking opportunity / CFS.
        """
        extracted = self._extract_details_from_page_content(
            content,
            name=name,
            description=description,
        )
        if not extracted:
            return opp
        return self._overwrite_core_fields_from_page(opp, extracted)

    @staticmethod
    def _llm_bool(value: Any) -> bool:
        if value is True:
            return True
        if isinstance(value, (int, float)) and value == 1:
            return True
        if isinstance(value, str) and value.strip().lower() in ("true", "yes", "1"):
            return True
        return False

    def verify_and_refresh_from_page_content(
        self,
        opp: Dict[str, Any],
        content: str,
        *,
        name: str = "",
        description: str = "",
    ) -> tuple[bool, str, Dict[str, Any]]:
        """
        LLM gate + field refresh in one call.

        Asks whether the scraped opportunity URL hosts an OPEN speaking/CFS opportunity
        for this event (apply-to-speak required). False for attend/RSVP/tickets, LinkedIn
        promos, sponsor/exhibitor hubs, academic papers/abstracts, featured-speaker-only
        pages, and contact-email-only pages. If yes, overwrites core fields from the same
        LLM response.

        Returns (ok, reason, updated_opp). reason is empty when ok.
        """
        content = (content or "").strip()
        if not content:
            return False, "Opportunity URL page content is empty.", opp

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return False, "OPENAI_API_KEY not configured for opportunity URL verification.", opp

        event_name = (opp.get("event_name") or opp.get("title") or "").strip()
        link = (opp.get("link") or opp.get("url") or "").strip()
        try:
            client = OpenAI(api_key=api_key)
            model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": self._verify_and_refresh_system_prompt()},
                    {
                        "role": "user",
                        "content": self.VERIFY_AND_REFRESH_USER_PROMPT_TEMPLATE.format(
                            event_name=event_name or "(not provided)",
                            location=(opp.get("location") or "").strip() or "(not provided)",
                            start_date=str(opp.get("start_date") or "(not provided)"),
                            end_date=str(opp.get("end_date") or "(not provided)"),
                            link=link or "(not provided)",
                            name=name or "(not provided)",
                            description=description or "(not provided)",
                            content=content[:8000],
                        ),
                    },
                ],
                temperature=0.1,
            )
            text = response.choices[0].message.content
            parsed = self._parse_llm_json_object(text) if text else None
        except Exception as e:
            return False, f"LLM verification failed for opportunity URL: {e}", opp

        if not parsed:
            return False, "LLM verification returned invalid JSON for opportunity URL.", opp

        if not self._llm_bool(parsed.get("hosts_speaking_opportunity")):
            reason = (parsed.get("reason") or "").strip() or (
                "LLM determined the opportunity URL does not host a speaking opportunity "
                "for this event."
            )
            return False, reason, opp

        updated = self._overwrite_core_fields_from_page(opp, parsed)
        return True, "", updated

    def extract_exact_dates_from_content(
        self,
        content: str,
        *,
        name: str = "",
        description: str = "",
    ) -> tuple[Optional[str], Optional[str]]:
        """Extract day-precision start/end dates only; returns (None, None) if not explicit."""
        from app.helpers.SpeakingOpportunityExtractor import _parse_date_to_iso

        # Prefer the portion of the page that mentions dates (dates often appear mid-page)
        text = (content or "").strip()
        window = text
        lower = text.lower()
        for needle in ("date", "when", "october", "november", "september", "2026", "2027", "2025"):
            idx = lower.find(needle)
            if idx >= 0:
                start = max(0, idx - 500)
                window = text[start : start + 6000]
                break
        if len(window) > 8000:
            window = window[:8000]

        extracted = self._extract_details_from_page_content(
            window, name=name, description=description
        )
        if not extracted:
            return None, None
        start_iso = _parse_date_to_iso(
            extracted.get("start_date") or extracted.get("date"), require_day=True
        )
        end_iso = _parse_date_to_iso(extracted.get("end_date"), require_day=True)
        if start_iso and not end_iso:
            end_iso = start_iso
        return start_iso, end_iso

    def _enrich_opportunity(self, opp: Dict[str, Any]) -> Dict[str, Any]:
        """Enrich a single opportunity by scraping its link and extracting via LLM."""
        link = (opp.get("link") or opp.get("url") or "").strip()
        if not link:
            return opp
        if not self._is_opportunity_incomplete(opp):
            return opp

        result = self.rapidapi_scraper.scrape(link)
        if not result.get("success"):
            return opp

        data = result.get("data", {})
        content = (data.get("content") or "").strip()
        name = data.get("name") or ""
        description = data.get("description") or ""
        og_url = data.get("ogUrl")

        if not content:
            return opp

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return opp

        try:
            client = OpenAI(api_key=api_key)
            model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": self._enricher_system_prompt()},
                    {
                        "role": "user",
                        "content": self._enricher_user_prompt_template().format(
                            name=name or "(not provided)",
                            description=description or "(not provided)",
                            content=content[:8000],
                        ),
                    },
                ],
                temperature=0.1,
            )
            text = response.choices[0].message.content
            enriched_data = self._parse_llm_json_object(text) if text else None
            if not enriched_data:
                return opp

            merged = self._merge_enriched(opp, enriched_data)
            raw_topics = merged.get("topics") or []
            raw_topic_values = [str(t).strip() for t in raw_topics if str(t or "").strip()]
            ai_predicted_topics = merged.get("aipredictedTopics") or []
            if raw_topic_values:
                allowed_topics = _filter_list_to_allowed(
                    raw_topic_values,
                    ALLOWED_TOPICS,
                    _ALLOWED_TOPICS_SET,
                    _ALLOWED_TOPICS_LOWER,
                )
                if allowed_topics:
                    merged["topics"] = allowed_topics
                    unmatched_raw_topics = [
                        topic for topic in raw_topic_values
                        if topic not in _ALLOWED_TOPICS_SET and topic.lower() not in _ALLOWED_TOPICS_LOWER
                    ]
                    if unmatched_raw_topics:
                        existing = ai_predicted_topics if isinstance(ai_predicted_topics, list) else []
                        merged["aipredictedTopics"] = [
                            topic for topic in [*existing, *unmatched_raw_topics]
                            if str(topic or "").strip()
                        ][:10]
                else:
                    merged["topics"] = []
                    merged["aipredictedTopics"] = raw_topic_values
            elif isinstance(ai_predicted_topics, list) and ai_predicted_topics:
                merged["topics"] = []
            else:
                merged["topics"] = self._ensure_topics_non_empty(merged)
            merged["speaking_format"] = _filter_speaking_format((merged.get("speaking_format") or "").strip())
            merged["delivery_mode"] = _filter_delivery_mode((merged.get("delivery_mode") or "").strip())
            raw_audiences = merged.get("target_audiences") or []
            merged["target_audiences"] = resolve_target_audiences(
                [str(a).strip() for a in raw_audiences if a],
                allowed=self.target_audiences,
                page_snippet=content[:2000],
                event_name=(merged.get("event_name") or ""),
                force_ai_if_empty=True,
            )
            if og_url:
                meta = merged.get("metadata")
                if not isinstance(meta, dict):
                    meta = {}
                meta["ogUrl"] = og_url
                merged["metadata"] = meta
            return merged
        except Exception:
            return opp

    def enrich_opportunities(self, opportunities: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
        """Enrich opportunities that have link but missing details."""
        enriched = []
        to_enrich = [o for o in opportunities if self._is_opportunity_incomplete(o) and (o.get("link") or o.get("url"))]
        if not to_enrich:
            return opportunities

        for opp in opportunities:
            if opp in to_enrich:
                enriched.append(self._enrich_opportunity(opp))
            else:
                enriched.append(opp)
        return enriched
