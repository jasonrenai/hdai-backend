"""ValidateAnswer — deterministic validation + confidence gates."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from app.services.onboarding_agent.analyze import AnalysisResult
from app.services.onboarding_agent.question_schema import QuestionDefinition
from app.services.speaker_profile_chatbot_steps import (
    CATALOG_STEPS,
    SKIPPABLE_STEPS,
    looks_like_invalid_location_answer,
    normalize_preferred_speaking_times,
    validate_and_normalize_location,
)

_EMAIL_RE = re.compile(
    r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"
    r"(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*\.[a-zA-Z]{2,}$"
)
_URL_RE = re.compile(r"^https?://", re.I)
_PHONE_RE = re.compile(r"(?:\+?\d[\d\s().-]{6,}\d)")
_MIN_PHONE_DIGITS = 10
_INVALID_PHONE_MESSAGE = (
    "That phone number doesn't look valid. "
    "Please share a valid phone number (with country code if possible)."
)


def _phone_digit_count(raw: str) -> int:
    return sum(1 for c in (raw or "") if c.isdigit())


def _looks_like_phone_attempt(message: str) -> bool:
    text = (message or "").strip()
    if not text:
        return False
    digits = _phone_digit_count(text)
    if digits < 7:
        return False
    compact = re.sub(r"[\s().+-]+", "", text)
    if not compact:
        return False
    return (digits / max(len(compact), 1)) >= 0.7


def _extract_phone_candidate(message: str, updates: Dict[str, Any]) -> str:
    phone = str(updates.get("phone_number") or "").strip()
    if phone:
        return phone
    phone_match = _PHONE_RE.search(message or "")
    if phone_match:
        return phone_match.group(0).strip()
    # Bare digit strings (e.g. "23695874") that fail the spaced phone regex
    if _looks_like_phone_attempt(message or ""):
        return re.sub(r"[^\d+]", "", (message or "").strip())
    return ""

CONFIDENCE_ACCEPT = 0.9
CONFIDENCE_CONFIRM = 0.7


@dataclass
class ValidationResult:
    valid: bool
    reason: str = ""
    message: str = ""
    normalized_updates: Dict[str, Any] = field(default_factory=dict)
    rejected_options: List[str] = field(default_factory=list)
    accepted_options: List[str] = field(default_factory=list)
    needs_confirmation: bool = False
    mark_step_done_off_list: bool = False  # catalog/pst all off-list → advance


def confidence_gate(confidence: float, *, ambiguous: bool = False) -> str:
    """
    Returns: accept | confirm | reject
    """
    if confidence > CONFIDENCE_ACCEPT and not ambiguous:
        return "accept"
    if confidence >= CONFIDENCE_CONFIRM:
        return "confirm" if ambiguous else "accept"
    return "reject"


def _filter_enum(values: List[str], allowed: List[str]) -> tuple[List[str], List[str]]:
    allowed_map = {a.strip().lower(): a for a in allowed if a and str(a).strip()}
    accepted: List[str] = []
    rejected: List[str] = []
    seen: Set[str] = set()
    for v in values:
        s = str(v or "").strip()
        if not s:
            continue
        key = s.lower()
        if key in allowed_map:
            canon = allowed_map[key]
            if canon.lower() not in seen:
                seen.add(canon.lower())
                accepted.append(canon)
        else:
            rejected.append(s)
    return accepted, rejected


def _as_str_list(raw: Any) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    if isinstance(raw, str) and raw.strip():
        return [p.strip() for p in re.split(r"[\n,;]+", raw) if p.strip()]
    return []


def _word_count(text: str) -> int:
    return len([w for w in re.split(r"\s+", (text or "").strip()) if w])


def validate_answer(
    *,
    question: Optional[QuestionDefinition],
    analysis: AnalysisResult,
    message: str,
    catalog: Optional[Dict[str, List[str]]] = None,
    client: Any = None,
    current_step: str = "",
) -> ValidationResult:
    """
    Deterministic validation for the current question's fields in profile_updates.
    Also retains multi-answer extras that pass basic type checks.
    """
    if analysis.gibberish:
        return ValidationResult(
            valid=False,
            reason="GIBBERISH",
            message="I'm sorry, I couldn't understand that. Could you answer the question again?",
        )

    updates = dict(analysis.profile_updates or {})
    step = (question.id if question else current_step) or ""
    catalog = catalog or {}
    normalized: Dict[str, Any] = {}

    # Pre-create contact heuristics should not be blocked by low LLM confidence
    # when the message clearly contains an email or phone number.
    msg = message or ""
    has_clear_email = bool(
        re.search(
            r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9].+\.[a-zA-Z]{2,}",
            msg,
        )
    )
    has_clear_phone = bool(_PHONE_RE.search(msg) and sum(1 for c in msg if c.isdigit()) >= 7)
    pre_create_steps = {
        "ask_identity",
        "prompt_welcome_and_contact",
        "post_welcome",
        "ready_to_create",
        "create_profile",
    }
    skip_confidence = step in pre_create_steps and (has_clear_email or has_clear_phone)

    gate = confidence_gate(analysis.confidence, ambiguous=analysis.clarification_needed)
    if (
        not skip_confidence
        and gate == "reject"
        and analysis.intent in ("ANSWER", "CHANGE_PREVIOUS")
    ):
        return ValidationResult(
            valid=False,
            reason="LOW_CONFIDENCE",
            message="I want to make sure I got that right — could you rephrase your answer?",
            needs_confirmation=False,
        )

    # Location hard gate
    if step == "location" and looks_like_invalid_location_answer(message or ""):
        return ValidationResult(
            valid=False,
            reason="INVALID_LOCATION",
            message=(
                "That doesn't look like a real city, state or province, and country. "
                "What city, state or province, and country are you based in? "
                "You can answer in one line (e.g. Austin, Texas, United States)."
            ),
        )

    # Catalog / preferred speaking time
    if step in CATALOG_STEPS:
        field = step
        raw_vals = _as_str_list(updates.get(field))
        if not raw_vals and analysis.answer:
            raw_vals = _as_str_list(analysis.answer.get(field) or analysis.answer.get("value"))
        # LLM attemptedValues are the primary off-list signal (not message regex).
        attempted = list(analysis.attempted_values or [])
        if not raw_vals and attempted:
            raw_vals = list(attempted)
        allowed = list(catalog.get(step) or (question.options if question else []) or [])
        accepted, rejected = _filter_enum(raw_vals, allowed)
        # Any attempted value not accepted is rejected
        for a in attempted:
            if a and a not in accepted and a not in rejected:
                rejected.append(a)
        if accepted:
            normalized[field] = accepted
            result = ValidationResult(
                valid=True,
                normalized_updates=normalized,
                accepted_options=accepted,
                rejected_options=rejected,
                needs_confirmation=(gate == "confirm"),
            )
            return result
        if raw_vals or attempted:
            # Off-list only — mark step done and advance (product rule for catalog).
            rej = rejected or raw_vals or attempted
            hint = (analysis.rejected_reason_hint or "").strip()
            msg = hint or (
                f"{', '.join(rej)} isn't on this list, but you can add "
                f"{'it' if len(rej) == 1 else 'those'} later from your speaker profile."
            )
            return ValidationResult(
                valid=True,
                normalized_updates={},
                rejected_options=rej,
                mark_step_done_off_list=True,
                message=msg,
            )
        # Empty extraction: trust LLM flags for meta/uncertain
        if analysis.uncertain:
            return ValidationResult(
                valid=False,
                reason="UNCERTAIN",
                message=(
                    "No problem — pick any that fit from the list below "
                    "(you can change them later from your profile). "
                    "If none fit, say so and we can move on."
                ),
            )
        if analysis.wants_update_previous or analysis.meta_question:
            return ValidationResult(
                valid=False,
                reason="META",
                message=(
                    "Yes — you can update a previous answer. Tell me which field to change and the new value, "
                    "or pick from the options below to continue."
                ),
            )
        return ValidationResult(
            valid=False,
            reason="INVALID_OPTION",
            message="Please select one or more from the available options.",
            rejected_options=rejected,
        )

    if step == "preferred_speaking_time":
        raw = updates.get("preferred_speaking_time")
        if raw is None and analysis.answer:
            raw = analysis.answer.get("preferred_speaking_time") or analysis.answer.get("value")
        # Prefer LLM attemptedValues over raw message scraping
        attempted = list(analysis.attempted_values or [])
        if raw is None or raw == "" or raw == []:
            raw = attempted if attempted else None

        def _filt(vals, allowed):
            return _filter_enum(vals if isinstance(vals, list) else _as_str_list(vals), allowed)[0]

        accepted = normalize_preferred_speaking_times(raw, _filt) if raw is not None else []
        raw_list = _as_str_list(raw)
        if not raw_list and attempted:
            raw_list = list(attempted)
        rejected = [x for x in raw_list if x not in accepted]
        for a in attempted:
            if a and a not in accepted and a not in rejected:
                rejected.append(a)

        if accepted:
            normalized["preferred_speaking_time"] = accepted
            return ValidationResult(
                valid=True,
                normalized_updates=normalized,
                accepted_options=accepted,
                rejected_options=rejected,
                needs_confirmation=(gate == "confirm"),
            )
        if raw_list or attempted:
            rej = rejected or raw_list or attempted
            hint = (analysis.rejected_reason_hint or "").strip()
            msg = hint or (
                f"{', '.join(rej)} isn't on the preferred speaking time list. "
                "Please choose one or more from the options below. "
                "You can add other speaking times later from your speaker profile."
            )
            return ValidationResult(
                valid=False,
                reason="OFF_LIST",
                message=msg,
                rejected_options=rej,
            )
        if analysis.uncertain:
            return ValidationResult(
                valid=False,
                reason="UNCERTAIN",
                message=(
                    "No problem — pick any that fit from the list below "
                    "(you can change them later from your profile)."
                ),
            )
        if analysis.wants_update_previous or analysis.meta_question:
            return ValidationResult(
                valid=False,
                reason="META",
                message=(
                    "Yes — you can update a previous answer. Tell me which field to change and the new value, "
                    "or pick a speaking time below to continue."
                ),
            )
        return ValidationResult(
            valid=False,
            reason="INVALID_OPTION",
            message=(
                "Please choose one or more from the preferred speaking times below. "
                "You can add other speaking times later from your speaker profile."
            ),
        )

    if step == "location":
        city = str(updates.get("address_city") or "").strip()
        state = str(updates.get("address_state") or "").strip()
        country = str(updates.get("address_country") or "").strip()
        # Try parse from single answer string
        if not (city and state and country):
            ans = analysis.answer or {}
            city = city or str(ans.get("address_city") or "").strip()
            state = state or str(ans.get("address_state") or "").strip()
            country = country or str(ans.get("address_country") or "").strip()
        if not (city and state and country) and message:
            parts = [p.strip() for p in re.split(r"[,|/]", message) if p.strip()]
            if len(parts) >= 3:
                city, state, country = parts[0], parts[1], parts[2]
            elif len(parts) == 2:
                city, country = parts[0], parts[1]
                state = parts[0]
        loc = validate_and_normalize_location(client, city, state, country)
        if not loc:
            return ValidationResult(
                valid=False,
                reason="INVALID_LOCATION",
                message=(
                    "That doesn't look like a real city, state or province, and country. "
                    "Please share city, state/province, and country in one line."
                ),
            )
        normalized.update(loc)
        return ValidationResult(
            valid=True,
            normalized_updates=normalized,
            needs_confirmation=(gate == "confirm"),
        )

    if step == "bio":
        bio = str(updates.get("bio") or (analysis.answer or {}).get("bio") or message or "").strip()
        words = _word_count(bio)
        min_w = (question.validation or {}).get("minWords", 20) if question else 20
        if words < max(8, min_w // 2):
            return ValidationResult(
                valid=False,
                reason="TOO_SHORT",
                message="Please share a professional bio in about 50–100 words.",
            )
        normalized["bio"] = bio
        return ValidationResult(valid=True, normalized_updates=normalized)

    if step == "social":
        for k in ("linkedin_url", "twitter", "facebook", "instagram"):
            v = updates.get(k)
            if v and isinstance(v, str) and v.strip():
                url = v.strip()
                if not _URL_RE.match(url) and "linkedin.com" in url.lower():
                    url = "https://" + url.lstrip("/")
                normalized[k] = url
        if not normalized:
            # Try map raw message URLs
            for m in re.finditer(r"https?://[^\s]+", message or ""):
                url = m.group(0).rstrip(".,)")
                low = url.lower()
                if "linkedin.com" in low:
                    normalized["linkedin_url"] = url
                elif "twitter.com" in low or "x.com" in low:
                    normalized["twitter"] = url
                elif "instagram.com" in low:
                    normalized["instagram"] = url
                elif "facebook.com" in low:
                    normalized["facebook"] = url
        if not normalized:
            return ValidationResult(
                valid=False,
                reason="MISSING",
                message="Please share at least one professional social URL, or say skip.",
            )
        return ValidationResult(valid=True, normalized_updates=normalized)

    if step == "video_links":
        links = _as_str_list(updates.get("video_links"))
        if not links:
            links = re.findall(r"https?://[^\s]+", message or "")
        good = [u.rstrip(".,)") for u in links if _URL_RE.match(u)]
        if not good:
            return ValidationResult(
                valid=False,
                reason="INVALID_URL",
                message="Please share a video URL, or say skip if you have none.",
            )
        normalized["video_links"] = good
        return ValidationResult(valid=True, normalized_updates=normalized)

    # Pre-create / create contact fields
    if step in (
        "ask_identity",
        "prompt_welcome_and_contact",
        "post_welcome",
        "ready_to_create",
        "create_profile",
    ):
        for k in ("full_name", "professional_title", "company", "phone_number"):
            v = updates.get(k)
            if isinstance(v, str) and v.strip():
                normalized[k] = v.strip()
        email = str(updates.get("email") or "").strip().lower()
        if not email:
            emails = re.findall(
                r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"
                r"(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*\.[a-zA-Z]{2,}",
                message or "",
            )
            if len(emails) == 1:
                email = emails[0].lower()
            elif len(emails) > 1:
                return ValidationResult(
                    valid=False,
                    reason="MULTI_EMAIL",
                    message="Please share exactly one email address you'd like to use.",
                )
        if email:
            if not _EMAIL_RE.match(email):
                return ValidationResult(
                    valid=False,
                    reason="INVALID_EMAIL",
                    message="That doesn't look like a valid email. Could you share a proper email address?",
                )
            normalized["email"] = email

        phone = str(normalized.get("phone_number") or "").strip()
        if not phone:
            phone = _extract_phone_candidate(message or "", updates)
        if phone:
            digits = _phone_digit_count(phone)
            if digits < _MIN_PHONE_DIGITS:
                # Keep any email already extracted; reject short/invalid phone.
                return ValidationResult(
                    valid=False,
                    reason="INVALID_PHONE",
                    message=_INVALID_PHONE_MESSAGE,
                    normalized_updates={k: v for k, v in normalized.items() if k != "phone_number"},
                )
            normalized["phone_number"] = phone
        elif _looks_like_phone_attempt(message or ""):
            return ValidationResult(
                valid=False,
                reason="INVALID_PHONE",
                message=_INVALID_PHONE_MESSAGE,
                normalized_updates=dict(normalized),
            )

        # Identity step needs at least a name — trust LLM greetingOnly flag
        if step == "ask_identity" and not normalized.get("full_name"):
            msg = (message or "").strip()
            if analysis.greeting_only or (msg and len(msg.split()) <= 2 and "@" not in msg and not _PHONE_RE.search(msg) and msg.lower().rstrip(".!") in {
                "hi", "hello", "hey", "howdy", "yo", "sup",
            }):
                return ValidationResult(
                    valid=False,
                    reason="GREETING",
                    message="Please share your professional name, title, and company.",
                )
            if (
                msg
                and len(msg.split()) <= 8
                and "@" not in msg
                and not _PHONE_RE.search(msg)
                and not analysis.greeting_only
            ):
                from app.services.speaker_profile_chatbot_steps import _clean_displayed_name

                normalized["full_name"] = _clean_displayed_name(msg) or msg
            else:
                # Keep any email/phone we already extracted; still need a name.
                return ValidationResult(
                    valid=False,
                    reason="MISSING_NAME",
                    message="Please share your professional name, title, and company.",
                    normalized_updates=normalized,
                )
        return ValidationResult(
            valid=bool(normalized),
            normalized_updates=normalized,
            reason="" if normalized else "EMPTY",
            message="" if normalized else "Could you share a bit more detail?",
        )

    # Generic: copy through known fields from updates for free-text / composite steps
    allowed_fields = set((question.fields if question else []) or [])
    # Always allow multi-answer extras that look like profile fields
    profile_keys = {
        "full_name",
        "professional_title",
        "company",
        "email",
        "phone_number",
        "address_city",
        "address_state",
        "address_country",
        "bio",
        "linkedin_url",
        "twitter",
        "facebook",
        "instagram",
        "preferred_speaking_time",
        "topics",
        "speaking_formats",
        "delivery_mode",
        "target_audiences",
        "talk_description",
        "key_takeaways",
        "past_speaking_examples",
        "video_links",
        "testimonial",
        "professional_memberships",
    }
    for k, v in updates.items():
        if k in profile_keys and v not in (None, "", []):
            # Current-step fields preferred; extras kept for multi-answer
            if k in allowed_fields or k not in CATALOG_STEPS:
                normalized[k] = v

    # Free-text fallback: put message into primary field
    if not normalized and step and step not in SKIPPABLE_STEPS:
        primary = list(allowed_fields)[0] if allowed_fields else None
        if primary in ("bio", "testimonial") or primary == "key_takeaways":
            if primary == "key_takeaways":
                normalized[primary] = [message.strip()] if message.strip() else []
            elif primary == "testimonial":
                normalized[primary] = [message.strip()] if message.strip() else []
            else:
                normalized[primary] = message.strip()
        elif primary == "talk_description" and message.strip():
            s = message.strip()
            normalized["talk_description"] = {"title": s[:200], "overview": s[:2000]}

    if not normalized:
        return ValidationResult(
            valid=False,
            reason="EMPTY",
            message="Could you answer that in a bit more detail?",
        )

    return ValidationResult(
        valid=True,
        normalized_updates=normalized,
        needs_confirmation=(gate == "confirm"),
    )
