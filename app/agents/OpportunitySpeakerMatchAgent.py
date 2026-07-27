"""
AI agent that checks whether a single opportunity matches a speaker profile.
Used after vector matching to filter opportunities before saving to matchedOpportunities.
"""
import json
import logging
import os
import re
from typing import Any, Dict

from openai import OpenAI

from app.helpers.PineconeOpportunityStore import OpportunityTextBuilder

logger = logging.getLogger(__name__)


def _summary_profile(profile: dict) -> str:
    """Build LLM summary: topics, speaking formats, delivery mode, target audiences only."""
    parts = []
    topics = OpportunityTextBuilder._to_str(profile.get("topics") or [])
    if topics:
        parts.append(f"Topics: {topics}")
    formats = profile.get("speaking_formats") or []
    if isinstance(formats, list):
        f_str = " ".join(OpportunityTextBuilder._item_text(s) for s in formats if s)
        if f_str:
            parts.append(f"Speaking formats: {f_str}")
    delivery = OpportunityTextBuilder._to_str(profile.get("delivery_mode"))
    if delivery:
        parts.append(f"Delivery mode: {delivery}")
    audiences = OpportunityTextBuilder._to_str(profile.get("target_audiences") or [])
    if audiences:
        parts.append(f"Target audiences: {audiences}")
    return "\n".join(parts) if parts else ""


def _summary_opportunity(opp: dict) -> str:
    """Build LLM summary: topics, speaking format, delivery mode, target audiences only."""
    parts = []
    topics = opp.get("topics") or []
    if isinstance(topics, list):
        t_str = ", ".join(str(t) for t in topics if t)
        if t_str:
            parts.append(f"Topics: {t_str}")
    fmt = (opp.get("speaking_format") or "").strip()
    if fmt:
        parts.append(f"Speaking format: {fmt}")
    delivery = (opp.get("delivery_mode") or "").strip()
    if delivery:
        parts.append(f"Delivery mode: {delivery}")
    audiences = opp.get("target_audiences") or []
    if isinstance(audiences, list):
        a_str = ", ".join(str(a) for a in audiences if a)
        if a_str:
            parts.append(f"Target audiences: {a_str}")
    return "\n".join(parts) if parts else ""


class OpportunitySpeakerMatchAgent:
    """
    Agent that uses an LLM to decide if an opportunity matches a speaker profile.
    Returns True when at least one speaker topic overlaps with an opportunity topic.
    """

    SYSTEM_PROMPT = """You are matching speaking opportunities to speaker profiles based on TOPICS ONLY.

Given a SPEAKER PROFILE and an OPPORTUNITY, compare their topic lists.

Rules:
- Return {"match": true} if at least ONE topic from the speaker overlaps with at least ONE topic on the opportunity.
- Overlap includes exact matches, close synonyms, and clearly related subtopics (e.g. "Leadership" and "Executive Leadership").
- Return {"match": false} only when NO speaker topic relates to ANY opportunity topic.
- Ignore speaking format, delivery mode, and target audience for this decision — topics only.

Reply with ONLY a JSON object with one key: "match" (boolean). Example: {"match": true} or {"match": false}.
Do not include any other text or explanation."""

    USER_PROMPT_TEMPLATE = """SPEAKER PROFILE:
{speaker_summary}

OPPORTUNITY:
{opportunity_summary}

Does at least one speaker topic match or relate to at least one opportunity topic? Reply with JSON only: {{"match": true}} or {{"match": false}}."""

    def __init__(self, openai_client: OpenAI = None):
        self._client = openai_client

    def _get_client(self) -> OpenAI:
        if self._client is None:
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                raise ValueError("OPENAI_API_KEY is required for OpportunitySpeakerMatchAgent")
            self._client = OpenAI(api_key=api_key)
        return self._client

    def is_match(self, speaker_profile: Dict[str, Any], opportunity: Dict[str, Any]) -> bool:
        """
        Return True if the opportunity is a good match for the speaker profile, False otherwise.
        Uses the LLM to compare profile and opportunity. On API/parse errors, returns False (exclude from match).
        """
        speaker_summary = _summary_profile(speaker_profile)
        opportunity_summary = _summary_opportunity(opportunity)
        if not speaker_summary or not opportunity_summary:
            return False
        try:
            client = self._get_client()
            model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": self.USER_PROMPT_TEMPLATE.format(
                            speaker_summary=speaker_summary,
                            opportunity_summary=opportunity_summary,
                        ),
                    },
                ],
                temperature=0.1,
            )
            text = (response.choices[0].message.content or "").strip()
            if not text:
                return False
            # Parse JSON (allow surrounding text)
            match = re.search(r"\{\s*\"match\"\s*:\s*(true|false)\s*\}", text, re.IGNORECASE)
            if match:
                obj = json.loads(match.group(0))
                return bool(obj.get("match", False))
            data = json.loads(text)
            return bool(data.get("match", False))
        except Exception as e:
            logger.warning("OpportunitySpeakerMatchAgent is_match failed: %s", e)
            return False
