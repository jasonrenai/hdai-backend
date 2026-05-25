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
    TARGET_AUDIENCES,
)

_ALLOWED_TOPICS_SET = set(ALLOWED_TOPICS)
_ALLOWED_TOPICS_LOWER = {t.lower(): t for t in ALLOWED_TOPICS}
_TOPICS_LIST_STR = ", ".join(f'"{t}"' for t in ALLOWED_TOPICS)

_SPEAKING_FORMATS_LOWER = {t.lower(): t for t in SPEAKING_FORMATS}
_SPEAKING_FORMATS_STR = ", ".join(f'"{t}"' for t in SPEAKING_FORMATS)
_DELIVERY_MODE_LOWER = {t.lower(): t for t in DELIVERY_MODE}
_DELIVERY_MODE_STR = ", ".join(f'"{t}"' for t in DELIVERY_MODE)
_TARGET_AUDIENCES_SET = set(TARGET_AUDIENCES)
_TARGET_AUDIENCES_LOWER = {t.lower(): t for t in TARGET_AUDIENCES}
_TARGET_AUDIENCES_STR = ", ".join(f'"{t}"' for t in TARGET_AUDIENCES)


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


def _filter_target_audiences_to_allowed(raw_list: List[str]) -> List[str]:
    return _filter_list_to_allowed(
        raw_list or [], TARGET_AUDIENCES, _TARGET_AUDIENCES_SET, _TARGET_AUDIENCES_LOWER
    )


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
    """

    ENRICHER_SYSTEM_PROMPT = """You are an expert at extracting event details from webpage content.
Given scraped content from an event page (markdown format), extract structured event information.

The content may include: event name, description, venue, location, date/time, topics, format, delivery mode, etc.

Extract and return a JSON object with EXACTLY these keys (no array, single object):
- event_name: Full name of the event (from page title/heading if not in content)
- location: City, country, or "Virtual" (e.g. "Leipzig, Germany", "New York, USA")
- topics: Array of relevant topics. You MUST choose ONLY from this exact list (use the exact string): """ + _TOPICS_LIST_STR + """. Pick one or more that best match the event. NEVER leave empty - pick at least one from the list.
- start_date: Event start date in ISO format YYYY-MM-DD (e.g. "2026-03-06"). Use first day of month if only month/year known.
- end_date: Event end date in ISO format YYYY-MM-DD. For one-day events use the SAME date as start_date.
- speaking_format: You MUST choose exactly ONE from this list (use the exact string): """ + _SPEAKING_FORMATS_STR + """
- delivery_mode: You MUST choose exactly ONE from this list (use the exact string), or empty string if unclear: """ + _DELIVERY_MODE_STR + """
- target_audiences: Array of audience types. You MUST choose ONLY from this exact list (use the exact strings): """ + _TARGET_AUDIENCES_STR + """. Empty array if none match.
- metadata: Object with description (1-2 sentences), venue name if mentioned, contact info if any. Include when present on the page: application_submission_deadline (ISO YYYY-MM-DD or omit), application_submission_closed (boolean, true only if explicitly closed / no longer accepting). Use {} for empty.

Return ONLY valid JSON, no other text. Extract only what is explicitly present; use empty string, [], or null for missing fields. topics must always have at least one item from the allowed list."""

    ENRICHER_USER_PROMPT_TEMPLATE = """Extract event details from this scraped page content.

Page name/title from scraper: {name}

Description snippet: {description}

Full content:
---
{content}
---

Return a single JSON object with keys: event_name, location, topics, start_date, end_date, speaking_format, delivery_mode, target_audiences, metadata. Use start_date and end_date in ISO format (YYYY-MM-DD); for one-day events set end_date equal to start_date. Use ONLY: topics from """ + _TOPICS_LIST_STR + """; speaking_format from """ + _SPEAKING_FORMATS_STR + """; delivery_mode from """ + _DELIVERY_MODE_STR + """; target_audiences from """ + _TARGET_AUDIENCES_STR + """."""

    def __init__(self, rapidapi_scraper: RapidAPIScraper = None):
        self.rapidapi_scraper = rapidapi_scraper or RapidAPIScraper()

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
                    {"role": "system", "content": self.ENRICHER_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": self.ENRICHER_USER_PROMPT_TEMPLATE.format(
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
            merged["target_audiences"] = _filter_target_audiences_to_allowed([str(a).strip() for a in raw_audiences if a])
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
