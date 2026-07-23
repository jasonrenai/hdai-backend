"""Target audience catalog helpers for opportunity scrape/enrich paths.

Audience names preferably come from Mongo speakerTargetAudeince; config is fallback only.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

from openai import OpenAI

from app.config.speaker_profile_chatbot import TARGET_AUDIENCES as FALLBACK_TARGET_AUDIENCES

logger = logging.getLogger(__name__)


def normalize_audience_catalog(names: Optional[Sequence[str]]) -> List[str]:
    """Deduplicate and clean audience names; fall back to config if empty."""
    out: List[str] = []
    seen = set()
    for raw in names or []:
        name = str(raw or "").strip()
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(name)
    if out:
        return out
    return list(FALLBACK_TARGET_AUDIENCES)


def audience_catalog_maps(names: Optional[Sequence[str]]) -> Tuple[List[str], set, Dict[str, str], str]:
    """Return (allowed list, set, lower->canonical map, prompt string)."""
    allowed = normalize_audience_catalog(names)
    allowed_set = set(allowed)
    allowed_lower = {t.lower(): t for t in allowed}
    allowed_str = ", ".join(f'"{t}"' for t in allowed)
    return allowed, allowed_set, allowed_lower, allowed_str


def filter_audiences_to_allowed(
    raw_list: Optional[Sequence[str]],
    allowed: Sequence[str],
    allowed_set: Optional[set] = None,
    allowed_lower: Optional[Dict[str, str]] = None,
) -> List[str]:
    """Keep only catalog values (exact or case-insensitive), deduplicated."""
    if allowed_set is None or allowed_lower is None:
        _, allowed_set, allowed_lower, _ = audience_catalog_maps(allowed)
    if not raw_list:
        return []
    seen = set()
    result: List[str] = []
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


def ai_closest_match_target_audiences(
    *,
    page_snippet: str = "",
    freeform_labels: Optional[Sequence[str]] = None,
    allowed: Sequence[str],
    event_name: str = "",
) -> List[str]:
    """
    Ask the LLM to map page context / freeform labels to closest allowed audience names.
    Returns only catalog strings (may be empty if API unavailable or no sensible match).
    """
    allowed_list, allowed_set, allowed_lower, allowed_str = audience_catalog_maps(allowed)
    if not allowed_list:
        return []

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return []

    labels = [str(x).strip() for x in (freeform_labels or []) if str(x or "").strip()]
    snippet = (page_snippet or "")[:4000].strip()
    if not labels and not snippet and not (event_name or "").strip():
        return []

    system = f"""You map speaking-event audience descriptions to a fixed catalog.
Return ONLY valid JSON: {{"target_audiences": ["..."]}}
Rules:
- Choose one or more closest matches from this exact list only: {allowed_str}
- Prefer the closest semantic match (e.g. developers/software engineers/AI practitioners → "Technical Professionals" when that label exists).
- Never invent names outside the list.
- If the event clearly has an audience, never return an empty array — pick the closest catalog value(s).
- Use exact catalog spelling."""

    user_payload = {
        "event_name": (event_name or "").strip(),
        "freeform_labels": labels,
        "page_snippet": snippet,
        "allowed_target_audiences": allowed_list,
    }

    try:
        client = OpenAI(api_key=api_key)
        model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=True)},
            ],
            temperature=0.1,
            timeout=45,
        )
        text = (response.choices[0].message.content or "").strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1]) if len(lines) > 2 else text
            if text.startswith("json"):
                text = text[4:].strip()
        data = json.loads(text)
        if not isinstance(data, dict):
            return []
        raw = data.get("target_audiences")
        if not isinstance(raw, list):
            return []
        return filter_audiences_to_allowed(raw, allowed_list, allowed_set, allowed_lower)
    except Exception as e:
        logger.warning("AI closest-match target audiences failed: %s", e)
        return []


def resolve_target_audiences(
    raw_list: Optional[Sequence[str]],
    *,
    allowed: Sequence[str],
    page_snippet: str = "",
    event_name: str = "",
    force_ai_if_empty: bool = True,
) -> List[str]:
    """
    Filter to catalog; if empty and force_ai_if_empty, run AI closest-match using
    freeform labels + page/event context.
    """
    allowed_list, allowed_set, allowed_lower, _ = audience_catalog_maps(allowed)
    filtered = filter_audiences_to_allowed(raw_list, allowed_list, allowed_set, allowed_lower)
    if filtered:
        return filtered
    if not force_ai_if_empty:
        return []
    return ai_closest_match_target_audiences(
        page_snippet=page_snippet,
        freeform_labels=raw_list,
        allowed=allowed_list,
        event_name=event_name,
    )


async def load_target_audience_names_from_db() -> List[str]:
    """Load audience names from speakerTargetAudeince; fall back to config if empty/error."""
    try:
        from app.models.SpeakerTargetAudience import SpeakerTargetAudienceModel

        docs = await SpeakerTargetAudienceModel().get_all()
        names = [str(d.get("name") or "").strip() for d in (docs or []) if d]
        names = [n for n in names if n]
        if names:
            logger.info("Loaded %d target audiences from speakerTargetAudeince", len(names))
            return normalize_audience_catalog(names)
        logger.warning(
            "speakerTargetAudeince returned no names; falling back to config TARGET_AUDIENCES"
        )
    except Exception as e:
        logger.warning(
            "Failed to load speakerTargetAudeince (%s); falling back to config TARGET_AUDIENCES",
            e,
        )
    return list(FALLBACK_TARGET_AUDIENCES)
