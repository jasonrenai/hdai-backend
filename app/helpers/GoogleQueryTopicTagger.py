"""Tag Google search queries with related speaker-profile topics.

Heuristic keyword/synonym match first; OpenAI fallback only when heuristic finds nothing.
Topics are constrained to speaker_profile_chatbot.TOPICS (exact catalog strings).
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Dict, List, Sequence, Tuple

from openai import OpenAI

from app.config.speaker_profile_chatbot import TOPICS as ALLOWED_TOPICS

logger = logging.getLogger(__name__)

_ALLOWED_SET = set(ALLOWED_TOPICS)
_ALLOWED_LOWER = {t.lower(): t for t in ALLOWED_TOPICS}
_ALLOWED_STR = ", ".join(f'"{t}"' for t in ALLOWED_TOPICS)

# Phrase (lowercase) -> catalog topic. Longer phrases are checked first.
_TOPIC_SYNONYMS: Dict[str, str] = {
    "artificial intelligence": "AI",
    "generative ai": "AI",
    "machine learning": "AI",
    "deep learning": "AI",
    "applied ai": "AI",
    "enterprise ai": "AI",
    "ai world": "AI",
    "ai summit": "AI",
    "ai conference": "AI",
    "ai marketing": "AI",
    "marketing ai": "AI",
    "digital marketing": "Marketing",
    "content marketing": "Marketing",
    "growth marketing": "Marketing",
    "brand strategy": "Marketing",
    "marketing strategy": "Marketing",
    "martech": "Marketing",
    "advertising": "Marketing",
    "customer experience": "Customer Experience",
    "data science": "Data Science",
    "e-commerce": "E-Commerce",
    "ecommerce": "E-Commerce",
    "ed tech": "EdTech",
    "edtech": "EdTech",
    "human resources": "Human Resources (HR)",
    "public relations": "Public Relations (PR)",
    "women in tech": "Women In Tech",
    "user experience": "UX/UI",
    "ux/ui": "UX/UI",
    "ux ui": "UX/UI",
    "financial services": "Financial Services",
    "fintech": "Financial Services",
    "executive leadership": "Executive Leadership",
    "c-suite": "Executive Leadership",
    "non-profit": "Nonprofit",
    "nonprofit": "Nonprofit",
    "software engineer": "Developer",
    "developers": "Developer",
    "developer": "Developer",
    "communications": "Communications",
    "entrepreneurship": "Entrepreneurship",
    "entrepreneurs": "Entrepreneurship",
    "entrepreneur": "Entrepreneurship",
    "education": "Education",
    "healthcare": "Health",
    "health": "Health",
    "franchise": "Franchise",
    "remortgage": "Remortgage",
    "retail": "Retail",
    "technology": "Technology",
    "b2b": "B2B",
    "b2c": "B2C",
    "startup": "Entrepreneurship",
    "startups": "Entrepreneurship",
}

# Short tokens need word-boundary matching to avoid false positives.
_SHORT_TOKEN_TOPICS: Dict[str, str] = {
    "ai": "AI",
    "ml": "AI",
    "hr": "Human Resources (HR)",
    "pr": "Public Relations (PR)",
    "cx": "Customer Experience",
    "ux": "UX/UI",
    "ui": "UX/UI",
    "cmo": "Marketing",
    "ceo": "Executive Leadership",
    "cfo": "Financial Services",
    "cto": "Technology",
    "tech": "Technology",
}

_SYNONYM_PHRASES_DESC = sorted(_TOPIC_SYNONYMS.keys(), key=len, reverse=True)
_TOPIC_NAMES_DESC = sorted(ALLOWED_TOPICS, key=len, reverse=True)


def filter_topics_to_allowed(raw_topics: Sequence[str] | None) -> List[str]:
    """Keep only catalog topic names (exact or case-insensitive), deduplicated."""
    if not raw_topics:
        return []
    seen = set()
    result: List[str] = []
    for t in raw_topics:
        s = (t or "").strip()
        if not s:
            continue
        if s in _ALLOWED_SET and s not in seen:
            result.append(s)
            seen.add(s)
            continue
        canonical = _ALLOWED_LOWER.get(s.lower())
        if canonical and canonical not in seen:
            result.append(canonical)
            seen.add(canonical)
    return result


def _add_topic(result: List[str], seen: set, topic: str) -> None:
    if topic in _ALLOWED_SET and topic not in seen:
        result.append(topic)
        seen.add(topic)


def match_topics_heuristic(query: str) -> List[str]:
    """Match query text against topic names and synonyms (case-insensitive)."""
    text = (query or "").strip()
    if not text:
        return []

    lower = text.lower()
    # Normalize common boolean-query punctuation so phrases still match.
    haystack = re.sub(r'[\\"()|]+', " ", lower)
    haystack = re.sub(r"\s+", " ", haystack).strip()

    result: List[str] = []
    seen: set = set()

    for phrase in _SYNONYM_PHRASES_DESC:
        if phrase in haystack:
            _add_topic(result, seen, _TOPIC_SYNONYMS[phrase])

    for name in _TOPIC_NAMES_DESC:
        name_lower = name.lower()
        # Skip short names that are handled via token map (e.g. "AI").
        if len(name_lower) <= 3 or name_lower in _SHORT_TOKEN_TOPICS:
            continue
        # Strip parenthetical aliases for matching, e.g. "Human Resources (HR)" -> "human resources"
        bare = re.sub(r"\s*\([^)]*\)\s*", " ", name_lower).strip()
        if bare and bare in haystack:
            _add_topic(result, seen, name)
        elif name_lower in haystack:
            _add_topic(result, seen, name)

    for token, topic in _SHORT_TOKEN_TOPICS.items():
        if re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", haystack):
            _add_topic(result, seen, topic)

    return result


def match_topics_ai(query: str) -> List[str]:
    """Ask the LLM to map a search query to closest allowed topic names."""
    text = (query or "").strip()
    if not text or not ALLOWED_TOPICS:
        return []

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        logger.warning("OPENAI_API_KEY missing; cannot AI-tag Google query topics")
        return []

    system = f"""You map Google search queries for speaking opportunities to a fixed topic catalog.
Return ONLY valid JSON: {{"relatedTopics": ["..."]}}
Rules:
- Choose one or more topics that the query is searching for from this exact list only: {_ALLOWED_STR}
- Prefer topics that reflect the subject matter of the search (e.g. AI conferences → "AI"; digital marketing CFP → "Marketing").
- A query may map to multiple topics when clearly relevant (e.g. AI marketing → "AI" and "Marketing").
- Never invent names outside the list.
- Use exact catalog spelling.
- If nothing fits, return an empty array."""

    user_payload = {
        "query": text[:4000],
        "allowed_topics": list(ALLOWED_TOPICS),
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
        raw_text = (response.choices[0].message.content or "").strip()
        if raw_text.startswith("```"):
            lines = raw_text.split("\n")
            raw_text = "\n".join(lines[1:-1]) if len(lines) > 2 else raw_text
            if raw_text.startswith("json"):
                raw_text = raw_text[4:].strip()
        data = json.loads(raw_text)
        if not isinstance(data, dict):
            return []
        raw = data.get("relatedTopics")
        if not isinstance(raw, list):
            return []
        return filter_topics_to_allowed([str(t) for t in raw])
    except Exception as e:
        logger.warning("AI related-topics match failed: %s", e)
        return []


def resolve_related_topics_with_source(query: str) -> Tuple[List[str], str]:
    """
    Resolve catalog topics for a query.

    Returns (topics, source) where source is \"heuristic\", \"ai\", or \"none\".
    """
    heuristic = match_topics_heuristic(query)
    if heuristic:
        return heuristic, "heuristic"
    ai_topics = match_topics_ai(query)
    if ai_topics:
        return ai_topics, "ai"
    return [], "none"


def resolve_related_topics(query: str) -> List[str]:
    """Heuristic first; AI only when heuristic returns no topics."""
    topics, _source = resolve_related_topics_with_source(query)
    return topics
