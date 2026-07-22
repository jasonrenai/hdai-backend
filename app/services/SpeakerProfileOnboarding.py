"""
Speaker Profile onboarding: step validation and normalization.
3-layer validation pipeline: Basic → Rule-based → AI Semantic.
Stateless: no session or DB. Uses OpenAI when validation_mode requires it.
"""
import os
import re
import json
from typing import Any, Dict, List, Optional, Tuple, Union
from openai import OpenAI

from app.config.speaker_profile_steps import (
    StepDefinition,
    get_step_by_name,
    get_next_step,
    is_last_step,
    step_to_response,
    get_first_step,
    STEPS,
)


# --- URL validation ---
_URL_PATTERN = re.compile(
    r"^https?://"
    r"(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|"
    r"localhost|"
    r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})"
    r"(?::\d+)?"
    r"(?:/?|[/?]\S+)$",
    re.IGNORECASE,
)

# LinkedIn profile URL only: https://www.linkedin.com/in/username or https://linkedin.com/in/username
_LINKEDIN_URL_PATTERN = re.compile(
    r"^https?://(www\.)?linkedin\.com/in/[\w\-]+/?(\?\S*)?$",
    re.IGNORECASE,
)
# Find a LinkedIn URL inside free-form text (for extraction)
_LINKEDIN_URL_SEARCH_PATTERN = re.compile(
    r"https?://(www\.)?linkedin\.com/in/[\w\-]+/?(\?\S*)?",
    re.IGNORECASE,
)

# Extract HTTP(S) URLs from free-form text (professional social step)
_SOCIAL_URL_SCRAPE_PATTERN = re.compile(r"https?://[^\s,\[\]<>\"]+", re.IGNORECASE)


def _classify_professional_social_url(url: str) -> Optional[str]:
    """Map URL to profile field name, or None if not a supported professional channel."""
    u = url.strip().rstrip(".,);]'\"")
    low = u.lower()
    if "linkedin.com" in low:
        return "linkedin_url"
    if "facebook.com" in low or "fb.com" in low or "m.facebook.com" in low:
        return "facebook"
    if "twitter.com" in low or "x.com" in low:
        return "twitter"
    if "instagram.com" in low:
        return "instagram"
    return None


def _parse_professional_social_urls(text: str) -> Dict[str, str]:
    """
    Parse text for LinkedIn, Facebook, X/Twitter, Instagram URLs.
    Returns dict subset of keys: linkedin_url, facebook, twitter, instagram.
    """
    out: Dict[str, str] = {}
    for m in _SOCIAL_URL_SCRAPE_PATTERN.finditer(text or ""):
        raw = m.group(0).strip().rstrip(".,);]'\"")
        field = _classify_professional_social_url(raw)
        if field and _is_valid_url(raw):
            out[field] = raw
    return out


# YouTube video URL: youtube.com/watch, youtu.be/, youtube.com/embed/, youtube.com/v/, supports query params
_YOUTUBE_URL_PATTERN = re.compile(
    r"^https?://(?:www\.)?(?:youtube\.com/(?:watch\?v=|embed/|v/)|youtu\.be/)[\w\-]+(?:\?[^\s]+)?$",
    re.IGNORECASE,
)

# Vimeo video URL: vimeo.com/123, player.vimeo.com/video/123, vimeo.com/channels/.../123, vimeo.com/groups/.../videos/123
_VIMEO_URL_PATTERN = re.compile(
    r"^https?://(?:www\.)?(?:vimeo\.com/(?:\d+|channels/[^/]+/\d+|groups/[^/]+/videos/\d+)|player\.vimeo\.com/video/\d+)(?:\?[^\s]*)?$",
    re.IGNORECASE,
)


def _is_valid_url(s: str) -> bool:
    if not s or not isinstance(s, str):
        return False
    s = s.strip()
    return bool(_URL_PATTERN.match(s))


def _is_valid_linkedin_url(s: str) -> bool:
    """True only if URL is a LinkedIn profile (linkedin.com/in/...). Rejects YouTube, etc."""
    if not s or not isinstance(s, str):
        return False
    s = s.strip()
    return bool(_LINKEDIN_URL_PATTERN.match(s))


def _is_valid_youtube_url(s: str) -> bool:
    """True only if URL is a YouTube video (youtube.com/watch, youtu.be/, etc.)."""
    if not s or not isinstance(s, str):
        return False
    s = s.strip()
    return bool(_YOUTUBE_URL_PATTERN.match(s))


def _is_valid_vimeo_url(s: str) -> bool:
    """True only if URL is a Vimeo video (vimeo.com/123, player.vimeo.com/video/123, etc.)."""
    if not s or not isinstance(s, str):
        return False
    s = s.strip()
    return bool(_VIMEO_URL_PATTERN.match(s))


def _is_valid_video_url(s: str) -> bool:
    """True if URL is a valid YouTube or Vimeo video URL."""
    return _is_valid_youtube_url(s) or _is_valid_vimeo_url(s)


def _check_gibberish(text: str) -> bool:
    text = text.strip()
    if not text:
        return True
    if len(set(text)) == 1 and len(text) > 5:
        return True
    if len(text) > 8 and len(set(text.lower())) <= 3:
        return True
    return False


# Full name: letters, spaces, hyphen, apostrophe; at least 2 words; each word >= 2 chars; no URLs
_FULL_NAME_REGEX = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿ' \-]+$")

# Email: standard pattern (no AI)
_EMAIL_REGEX = re.compile(
    r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*\.[a-zA-Z]{2,}$"
)

# Unicode space-like characters to normalize to ASCII space (e.g. from paste or mobile)
_UNICODE_SPACES = re.compile(r"[\u00a0\u2000-\u200b\u202f\u205f\u3000\ufeff]+")


def _normalize_name_for_validation(name: str) -> str:
    """Strip and normalize Unicode spaces to ASCII space so 'Max\u00a0Doe' passes like 'Max Doe'."""
    if not name or not isinstance(name, str):
        return ""
    name = name.strip()
    name = _UNICODE_SPACES.sub(" ", name)
    return " ".join(name.split())


def validate_email(value: str) -> bool:
    """Regex-only validation for email. No AI."""
    if not value or not isinstance(value, str):
        return False
    s = value.strip()
    if not s or len(s) > 254:
        return False
    return bool(_EMAIL_REGEX.match(s))


def validate_full_name(name: str) -> bool:
    """Strict code-only validation for full_name. No AI."""
    name = _normalize_name_for_validation(name)
    if not name:
        return False
    if "http" in name.lower() or "www." in name.lower():
        return False
    words = name.split()
    if len(words) < 2:
        return False
    for w in words:
        if len(w) < 2:
            return False
    return bool(_FULL_NAME_REGEX.match(name))


def split_input(text: str) -> List[str]:
    if not text or not isinstance(text, str):
        return []
    t = text.replace(" and ", ",").replace(" or ", ",")
    parts = [p.strip() for p in t.split(",") if p.strip()]
    return parts


def split_input_topics(text: str) -> List[str]:
    if not text or not isinstance(text, str):
        return []
    t = text.replace(" and ", ",").replace(" or ", ",").replace("&", ",").replace("/", ",")
    parts = [p.strip() for p in re.split(r",", t) if p and p.strip()]
    return parts


def _validate_basic(
    step: StepDefinition, answer: Union[str, List[str], List[dict]]
) -> Tuple[Optional[Union[str, List[str], List[dict]]], Optional[str]]:
    if step.required:
        if isinstance(answer, str):
            if not answer or not answer.strip():
                return None, "This field is required."
        elif isinstance(answer, list):
            # Allow list of strings (enum) or list of dicts (topics selection)
            if not answer:
                return None, "At least one value is required."
            if step.validation_mode in ("topics_multiselect", "target_audiences_multiselect"):
                if not any(isinstance(a, dict) and (a.get("_id") or a.get("slug") or a.get("name")) for a in answer):
                    return None, "At least one value is required."
            elif not any(isinstance(a, str) and a.strip() for a in answer):
                return None, "At least one value is required."
        else:
            return None, "Invalid input type."

    # topics_multiselect / target_audiences_multiselect: accept list of objects (selection) or string (text)
    if step.validation_mode in ("topics_multiselect", "target_audiences_multiselect"):
        if isinstance(answer, list):
            return answer, None
        if isinstance(answer, str):
            if step.required and not answer.strip():
                return None, "This field is required."
            return answer.strip(), None
        return None, "Invalid input type."

    if step.validation_type in ("string", "textarea", "url"):
        if isinstance(answer, list):
            answer = answer[0] if answer else ""
        if not isinstance(answer, str):
            return None, f"Expected a string, got {type(answer).__name__}."
        answer = answer.strip()
        if step.required and not answer:
            return None, "This field is required."
        # Only enforce min_length when step is required; optional steps need short refusal text (e.g. "I do not have any") to reach refusal detection
        if step.required and step.min_length and len(answer) < step.min_length:
            return None, f"Input must be at least {step.min_length} characters."
        if step.max_length and len(answer) > step.max_length:
            return None, f"Input must be no more than {step.max_length} characters."
        return answer, None

    elif step.validation_type in ("array_of_strings", "array_of_urls", "enum"):
        if isinstance(answer, str):
            answer = [answer] if answer.strip() else []
        if not isinstance(answer, list):
            return None, f"Expected a list, got {type(answer).__name__}."
        normalized = [a.strip() for a in answer if isinstance(a, str) and a.strip()]
        if step.required and not normalized:
            return None, "At least one value is required."
        if step.min_length and len(normalized) < step.min_length:
            return None, f"At least {step.min_length} value(s) required."
        return normalized, None

    return None, "Unsupported validation type."


def _validate_rule_based(
    step: StepDefinition,
    normalized_answer: Union[str, List[str]],
    source: str,
) -> Tuple[Optional[Union[str, List[str]]], Optional[str]]:
    if step.validation_mode == "url_only":
        if step.validation_type == "url":
            if step.step_name == "linkedin_url":
                if not _is_valid_linkedin_url(normalized_answer):
                    return None, "Please enter a valid LinkedIn profile URL (e.g. https://www.linkedin.com/in/yourprofile)."
                return normalized_answer, None
            if not _is_valid_url(normalized_answer):
                return None, "Please enter a valid URL."
            return normalized_answer, None
        elif step.validation_type == "array_of_urls":
            if isinstance(normalized_answer, str):
                normalized_answer = split_input(normalized_answer)
            else:
                # User sent one string with comma/and/or - flatten then split into separate URLs
                raw = " ".join(str(x).strip() for x in normalized_answer if str(x).strip())
                normalized_answer = split_input(raw)
            if step.step_name == "video_links":
                # video_links: YouTube or Vimeo URLs only; keep valid ones, INVALID only if none valid
                valid = [u for u in normalized_answer if _is_valid_video_url(u)]
                if not valid:
                    return None, "Please enter at least one valid YouTube or Vimeo video URL (e.g. https://www.youtube.com/watch?v=...)."
                return list(dict.fromkeys(valid)), None
            invalid_urls = [u for u in normalized_answer if not _is_valid_url(u)]
            if invalid_urls:
                return None, f"Invalid URL(s): {', '.join(invalid_urls[:3])}"
            return list(dict.fromkeys(normalized_answer)), None

    # strict_enum_code: pure code, case-insensitive match; split comma-separated; accept all valid values, re-ask only if none valid
    if step.validation_mode == "strict_enum_code":
        if not step.allowed_values:
            return None, "Step has no allowed values."
        allowed_lower = {v.strip().lower(): v for v in step.allowed_values}
        if isinstance(normalized_answer, str):
            parts = [p.strip() for p in normalized_answer.split(",") if p.strip()]
        else:
            parts = [str(a).strip() for a in normalized_answer if str(a).strip()]
        normalized = []
        for a in parts:
            key = a.lower()
            if key in allowed_lower:
                normalized.append(allowed_lower[key])
        if not normalized:
            return None, f"No valid selection. Allowed: {step.allowed_values}"
        return list(dict.fromkeys(normalized)), None

    return normalized_answer, None


def _validation_ai_gibberish_check(
    client: OpenAI,
    step: StepDefinition,
    text: str,
) -> Dict[str, Any]:
    """Return INVALID if text is gibberish/random/keyboard smash; VALID if meaningful."""
    prompt = f"""
Question: {step.question}
User response: {text}

Is this meaningful content (real answer, real topics, real text) or gibberish/random/keyboard smash/nonsense?
- If the user wrote real words, topics, or meaningful content, return VALID.
- If it is random characters, nonsense, "asdf", keyboard mashing, or clearly not a real answer, return INVALID.

Return JSON ONLY: {{ "status": "VALID" | "INVALID", "reason_code": "GIBBERISH" if INVALID else "OK" }}
"""
    try:
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0,
            messages=[
                {"role": "system", "content": "Return ONLY JSON. One of: {\"status\": \"VALID\", \"reason_code\": \"OK\"} or {\"status\": \"INVALID\", \"reason_code\": \"GIBBERISH\"}"},
                {"role": "user", "content": prompt},
            ],
            timeout=10,
        )
        obj = _parse_validation_ai_json(completion.choices[0].message.content or "")
        if not obj:
            return {"status": "INVALID", "reason_code": "GIBBERISH", "normalized_value": None}
        status = obj.get("status")
        if status == "VALID":
            return {"status": "VALID", "reason_code": "OK", "normalized_value": None}
        return {"status": "INVALID", "reason_code": "GIBBERISH", "normalized_value": None}
    except Exception:
        return {"status": "INVALID", "reason_code": "GIBBERISH", "normalized_value": None}


def _parse_validation_ai_json(content: str) -> Optional[Dict[str, Any]]:
    content = (content or "").strip()
    if content.startswith("```"):
        lines = content.split("\n")
        content = "\n".join(l for l in lines if l.strip() and not l.strip().startswith("```"))
    try:
        obj = json.loads(content)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


# Reason codes from semantic AI that we override to VALID (writing-coach style; not reject reasons)
SEMANTIC_OVERRIDE_REASON_CODES = {"TOO_VAGUE", "NEEDS_MORE_DETAIL", "UNCLEAR", "NOT_SPECIFIC", "COULD_BE_BETTER"}

# All allowed reason_codes for _validation_ai_intent (reject-only + override-to-valid)
_VALIDATION_AI_INTENT_REASON_CODES = [
    "EMPTY", "GIBBERISH", "IRRELEVANT", "SPAM", "UNRELATED", "LOW_EFFORT",
    "TOO_VAGUE", "NEEDS_MORE_DETAIL", "UNCLEAR", "NOT_SPECIFIC", "COULD_BE_BETTER",
]


def _validation_ai_intent(
    client: OpenAI,
    step: StepDefinition,
    user_text: str,
    profile_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    allowed_reason_codes = _VALIDATION_AI_INTENT_REASON_CODES
    context_blurb = ""
    if profile_context:
        context_blurb = f"\nCurrent profile (for context only): {json.dumps(profile_context)}"
    prompt = f"""
You are ValidationAI. You are a semantic sanity check only, not a writing coach.
{context_blurb}
Question field: {step.step_name}
Question: {step.question}
User response: {user_text}

Only mark INVALID if: the content is empty, obvious gibberish, completely irrelevant to the question, or spam.
Do NOT reject for: too broad, too general, too long, too short, lacking specificity, or style issues.
If the answer is meaningful and related to the question, return VALID.

Return JSON ONLY: {{ "status": "VALID" | "INVALID", "reason_code": one of {allowed_reason_codes}, "normalized_value": null }}
"""
    try:
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0,
            messages=[
                {"role": "system", "content": "Return ONLY JSON. No extra text."},
                {"role": "user", "content": prompt},
            ],
            timeout=10,
        )
        obj = _parse_validation_ai_json(completion.choices[0].message.content or "")
        if not obj:
            return {"status": "INVALID", "reason_code": "AI_UNAVAILABLE", "normalized_value": None}
        status = obj.get("status")
        reason_code = obj.get("reason_code")
        if status not in ("VALID", "INVALID") or reason_code not in allowed_reason_codes:
            return {"status": "INVALID", "reason_code": "AI_UNAVAILABLE", "normalized_value": None}
        return {"status": status, "reason_code": reason_code, "normalized_value": None}
    except Exception:
        return {"status": "INVALID", "reason_code": "AI_UNAVAILABLE", "normalized_value": None}


def _validation_ai_topics_normalize(
    client: OpenAI,
    step: StepDefinition,
    items: List[str],
    profile_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    allowed_reason_codes = ["EMPTY", "LOW_EFFORT", "GIBBERISH", "SPAM", "UNRELATED"]
    context_blurb = f"\nCurrent profile (for context only): {json.dumps(profile_context)}" if profile_context else ""
    prompt = f"""
You are ValidationAI for speaker topics. There is no predefined list of allowed topics.
{context_blurb}
Question: {step.question}
Input items (already split from user text): {json.dumps(items)}

Your goal: Decide if the content is meaningful (not gibberish, random, or spam). If yes, return VALID.
- One topic or many is fine. Return VALID if at least one valid topic.
- When VALID: return normalized_value as a JSON array of strings (e.g. ["Environment"] or ["Climate", "Sustainability"]). You may correct spelling and normalize terms; drop only gibberish/meaningless/low-effort items. We already split comma/and/or-separated input in code; you can return the array for saving individually.
- Return INVALID only for: empty, all gibberish, low-effort, spam, or completely unrelated.

Return JSON ONLY: {{ "status": "VALID" | "INVALID", "reason_code": one of {allowed_reason_codes}, "normalized_value": array of strings or null }}
"""
    try:
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0,
            messages=[
                {"role": "system", "content": "Return ONLY JSON. When status is VALID, normalized_value must be a JSON array of strings, e.g. [\"Environment\"], even for a single topic."},
                {"role": "user", "content": prompt},
            ],
            timeout=15,
        )
        obj = _parse_validation_ai_json(completion.choices[0].message.content or "")
        if not obj:
            return {"status": "INVALID", "reason_code": "AI_UNAVAILABLE", "normalized_value": None}
        status = obj.get("status")
        reason_code = obj.get("reason_code") or "UNRELATED"
        normalized_value = obj.get("normalized_value")
        if status not in ("VALID", "INVALID"):
            return {"status": "INVALID", "reason_code": "AI_UNAVAILABLE", "normalized_value": None}
        if status == "VALID":
            if not isinstance(normalized_value, list):
                if isinstance(normalized_value, str) and normalized_value.strip():
                    normalized_value = [normalized_value.strip()]
                else:
                    return {"status": "INVALID", "reason_code": "EMPTY" if normalized_value is None or (isinstance(normalized_value, str) and not normalized_value.strip()) else "AI_UNAVAILABLE", "normalized_value": None}
            cleaned = [str(x).strip() for x in normalized_value if str(x).strip()]
            if not cleaned:
                return {"status": "INVALID", "reason_code": "EMPTY", "normalized_value": None}
            return {"status": "VALID", "reason_code": "OK", "normalized_value": list(dict.fromkeys(cleaned))}
        return {"status": "INVALID", "reason_code": reason_code if reason_code in allowed_reason_codes else "UNRELATED", "normalized_value": None}
    except Exception:
        return {"status": "INVALID", "reason_code": "AI_UNAVAILABLE", "normalized_value": None}


def _reason_code_from_basic_error(err: str) -> str:
    e = (err or "").lower()
    if "required" in e or "at least one" in e:
        return "EMPTY"
    if "at least" in e and "characters" in e:
        return "TOO_SHORT"
    if "no more than" in e:
        return "TOO_LONG"
    if "expected" in e:
        return "TYPE_MISMATCH"
    return "BASIC_INVALID"


def _reason_code_from_rule_error(err: str) -> str:
    e = (err or "").lower()
    if "url" in e:
        return "INVALID_URL"
    if "invalid value" in e or "allowed" in e:
        return "ENUM_INVALID"
    return "RULE_INVALID"


def _validation_ai_full_name_extract(client: OpenAI, user_text: str) -> Dict[str, Any]:
    """Extract the person's full name from free-form text. Returns REFUSAL when user declines; INVALID when no real full name."""
    prompt = f"""
The user was asked for their full name. They replied: "{user_text}"

Extract the person's full name based on user intent. Return it as normalized_value with standard capitalization (e.g. "First Last").
- If they wrote a sentence like "My name is Manish Gautam" or "I'm John Smith", extract and return only the name: "Manish Gautam" or "John Smith".
- If they already wrote just their name in any capitalization (e.g. "Mike tyson", "mike tyson", "MANISH GAUTAM"), accept it and return it normalized to "First Last" (e.g. "Mike Tyson", "Manish Gautam"). Do NOT reject based on capitalization—users may type however they want; check intent, not formatting.
- In normalized_value always use standard capitalization (first letter of each name part) for storage.
- If the user declines to share their name (e.g. "I don't want to share", "I prefer not to say", "I'd rather not"), return INVALID with reason_code "REFUSAL".
- If you cannot identify a real person's full name (at least first and last), return INVALID with reason_code "INVALID_FULL_NAME".
- If the reply has one plausible first name but the rest is clearly not a real name (e.g. random letters, keyboard smash like "Akash ksdgjsdgrwgb"), return INVALID with reason_code "INVALID_FULL_NAME". Both first and last must look like real names. Reject only on intent (gibberish/refusal), never on capitalization or typing style.

Return JSON ONLY: {{ "status": "VALID" | "INVALID", "reason_code": "OK" | "REFUSAL" | "INVALID_FULL_NAME", "normalized_value": "Extracted Full Name" or null }}
"""
    try:
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0,
            messages=[
                {"role": "system", "content": "Return ONLY JSON. Judge user intent: accept any capitalization (e.g. Mike tyson, mike tyson). When status is VALID, normalized_value must be the extracted full name with standard capitalization (First Last). Reject only for REFUSAL or when both first and last are not real-looking names (gibberish). When INVALID, use reason_code REFUSAL if user declined, else INVALID_FULL_NAME. normalized_value must be null when INVALID."},
                {"role": "user", "content": prompt},
            ],
            timeout=10,
        )
        obj = _parse_validation_ai_json(completion.choices[0].message.content or "")
        if not obj:
            return {"status": "INVALID", "reason_code": "INVALID_FULL_NAME", "normalized_value": None}
        status = obj.get("status")
        normalized_value = obj.get("normalized_value")
        reason = obj.get("reason_code") or "INVALID_FULL_NAME"
        if status == "VALID" and isinstance(normalized_value, str) and normalized_value.strip():
            return {"status": "VALID", "reason_code": "OK", "normalized_value": normalized_value.strip()}
        return {"status": "INVALID", "reason_code": reason if reason in ("REFUSAL", "INVALID_FULL_NAME") else "INVALID_FULL_NAME", "normalized_value": None}
    except Exception:
        return {"status": "INVALID", "reason_code": "INVALID_FULL_NAME", "normalized_value": None}


def _validation_ai_email_extract(client: OpenAI, user_text: str) -> Dict[str, Any]:
    """Extract email from free-form text. Format is verified in code with regex; AI does not judge real/temporary."""
    prompt = f"""
The user was asked for their email address. They replied: "{user_text}"

Extract the single email address from the text and return it.
- From "my email is john@example.com" or "it's yabaf75886@bitoini.com" extract only the email: john@example.com or yabaf75886@bitoini.com.
- If they wrote just the email (e.g. "john@example.com"), return that.
- If the user declines to share their email (e.g. "I don't want to share", "I prefer not to give my email"), return INVALID with reason_code "REFUSAL".
- Do NOT reject or return INVALID based on whether the email looks temporary or disposable. Our system will verify format separately.
- Return INVALID with reason_code "INVALID_EMAIL" only if you cannot find exactly one email-like substring (e.g. no email, or multiple emails and unclear which one).

Return JSON ONLY: {{ "status": "VALID" | "INVALID", "reason_code": "OK" | "REFUSAL" | "INVALID_EMAIL", "normalized_value": "extracted@email.com" or null }}
"""
    try:
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0,
            messages=[
                {"role": "system", "content": "Return ONLY JSON. Extract the email the user gave. Use reason_code REFUSAL when user declines to share; INVALID_EMAIL when no email found. Do not judge if it is temporary or disposable. When status is VALID, normalized_value must be the single extracted email string, lowercase. When INVALID, normalized_value must be null."},
                {"role": "user", "content": prompt},
            ],
            timeout=10,
        )
        obj = _parse_validation_ai_json(completion.choices[0].message.content or "")
        if not obj:
            return {"status": "INVALID", "reason_code": "INVALID_EMAIL", "normalized_value": None}
        status = obj.get("status")
        normalized_value = obj.get("normalized_value")
        reason = obj.get("reason_code") or "INVALID_EMAIL"
        if status == "VALID" and isinstance(normalized_value, str) and normalized_value.strip():
            return {"status": "VALID", "reason_code": "OK", "normalized_value": normalized_value.strip().lower()}
        return {"status": "INVALID", "reason_code": reason if reason in ("REFUSAL", "INVALID_EMAIL") else "INVALID_EMAIL", "normalized_value": None}
    except Exception:
        return {"status": "INVALID", "reason_code": "INVALID_EMAIL", "normalized_value": None}


def _validation_ai_linkedin_refusal(client: OpenAI, user_text: str) -> Dict[str, Any]:
    """Detect skip/defer for social URLs (no links in message). REFUSAL allows skip; INVALID if gibberish."""
    prompt = f"""
The user was asked to share professional social media URLs (LinkedIn, Facebook, X, Instagram, etc.). They replied: "{user_text}"

Decide if they are skipping, deferring to later, or have none to share (e.g. "I'll add in my profile", "update later", "skip", "none", "prefer not", "don't have any") OR if they seem to be attempting to share links or unrelated gibberish.
- If they clearly skip, defer to updating their profile later, or have no links to share, return status "VALID" with "refusal": true.
- If the message looks like they tried to paste URLs but failed, or is unrelated noise, return status "INVALID" with reason_code "INVALID_URL".

Return JSON ONLY: {{ "status": "VALID" | "INVALID", "reason_code": "REFUSAL" | "INVALID_URL", "refusal": true only when status is VALID and they declined/deferred }}
"""
    try:
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0,
            messages=[
                {"role": "system", "content": "Return ONLY JSON. If user skips, defers to profile update later, or has no social URLs, return {\"status\": \"VALID\", \"reason_code\": \"REFUSAL\", \"refusal\": true}. If gibberish or failed URL attempt without clear skip intent, return {\"status\": \"INVALID\", \"reason_code\": \"INVALID_URL\"}. No other fields."},
                {"role": "user", "content": prompt},
            ],
            timeout=10,
        )
        obj = _parse_validation_ai_json(completion.choices[0].message.content or "")
        if not obj:
            return {"status": "INVALID", "reason_code": "INVALID_URL", "refusal": False}
        if obj.get("status") == "VALID" and obj.get("refusal") is True:
            return {"status": "VALID", "reason_code": "REFUSAL", "refusal": True}
        return {"status": "INVALID", "reason_code": "INVALID_URL", "refusal": False}
    except Exception:
        return {"status": "INVALID", "reason_code": "INVALID_URL", "refusal": False}


def _validation_ai_past_speaking_refusal(client: OpenAI, user_text: str) -> Dict[str, Any]:
    """Detect if user is declining or has no past examples. Returns refusal=True to allow skip."""
    prompt = f"""
The user was asked for past speaking examples or events. They replied: "{user_text}"

Decide if they are declining or have none (e.g. "I don't have any", "I prefer not to share", "skip", "none", "no examples yet") or if they are providing actual examples/events.
- If they clearly decline or say they have no past examples, return status "VALID" with "refusal": true.
- If they are providing examples or events (even brief), return status "INVALID" with reason_code "EMPTY" (we will accept and validate the content separately).

Return JSON ONLY: {{ "status": "VALID" | "INVALID", "reason_code": "REFUSAL" | "EMPTY", "refusal": true only when status is VALID and they declined or have none }}
"""
    try:
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0,
            messages=[
                {"role": "system", "content": "Return ONLY JSON. If user declines or has no examples, return {\"status\": \"VALID\", \"reason_code\": \"REFUSAL\", \"refusal\": true}. If they provided examples/events, return {\"status\": \"INVALID\", \"reason_code\": \"EMPTY\"}. No other fields."},
                {"role": "user", "content": prompt},
            ],
            timeout=10,
        )
        obj = _parse_validation_ai_json(completion.choices[0].message.content or "")
        if not obj:
            return {"status": "INVALID", "reason_code": "EMPTY", "refusal": False}
        if obj.get("status") == "VALID" and obj.get("refusal") is True:
            return {"status": "VALID", "reason_code": "REFUSAL", "refusal": True}
        return {"status": "INVALID", "reason_code": "EMPTY", "refusal": False}
    except Exception:
        return {"status": "INVALID", "reason_code": "EMPTY", "refusal": False}


def _validation_ai_video_links_refusal(client: OpenAI, user_text: str) -> Dict[str, Any]:
    """Detect if user is declining or has no video links. Returns refusal=True to allow skip."""
    prompt = f"""
The user was asked for links to their speaking videos. They replied: "{user_text}"

Decide if they are declining or have none (e.g. "I don't have any", "I prefer not to share", "skip", "none", "no videos yet") or if they are providing actual video URLs.
- If they clearly decline or say they have no video links, return status "VALID" with "refusal": true.
- If they are providing URL(s) or something that looks like a link, return status "INVALID" with reason_code "INVALID_URL".

Return JSON ONLY: {{ "status": "VALID" | "INVALID", "reason_code": "REFUSAL" | "INVALID_URL", "refusal": true only when status is VALID and they declined or have none }}
"""
    try:
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0,
            messages=[
                {"role": "system", "content": "Return ONLY JSON. If user declines or has no videos, return {\"status\": \"VALID\", \"reason_code\": \"REFUSAL\", \"refusal\": true}. If they provided URL(s), return {\"status\": \"INVALID\", \"reason_code\": \"INVALID_URL\"}. No other fields."},
                {"role": "user", "content": prompt},
            ],
            timeout=10,
        )
        obj = _parse_validation_ai_json(completion.choices[0].message.content or "")
        if not obj:
            return {"status": "INVALID", "reason_code": "INVALID_URL", "refusal": False}
        if obj.get("status") == "VALID" and obj.get("refusal") is True:
            return {"status": "VALID", "reason_code": "REFUSAL", "refusal": True}
        return {"status": "INVALID", "reason_code": "INVALID_URL", "refusal": False}
    except Exception:
        return {"status": "INVALID", "reason_code": "INVALID_URL", "refusal": False}


def _validation_ai_testimonial_refusal(client: OpenAI, user_text: str) -> Dict[str, Any]:
    """Detect if user is declining or has no testimonials. Returns refusal=True to allow skip."""
    prompt = f"""
The user was asked whether they have testimonials from past speaking. They replied: "{user_text}"

Decide if they are declining or have none (e.g. "I don't have any", "I prefer not to share", "skip", "none", "no testimonials") or if they are sharing testimonial text.
- If they clearly decline or say they have no testimonials, return status "VALID" with "refusal": true.
- If they are providing testimonial content, return status "INVALID" with reason_code "EMPTY".

Return JSON ONLY: {{ "status": "VALID" | "INVALID", "reason_code": "REFUSAL" | "EMPTY", "refusal": true only when status is VALID and they declined or have none }}
"""
    try:
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0,
            messages=[
                {"role": "system", "content": "Return ONLY JSON. If user declines or has no testimonials, return {\"status\": \"VALID\", \"reason_code\": \"REFUSAL\", \"refusal\": true}. If they provided testimonials, return {\"status\": \"INVALID\", \"reason_code\": \"EMPTY\"}. No other fields."},
                {"role": "user", "content": prompt},
            ],
            timeout=10,
        )
        obj = _parse_validation_ai_json(completion.choices[0].message.content or "")
        if not obj:
            return {"status": "INVALID", "reason_code": "EMPTY", "refusal": False}
        if obj.get("status") == "VALID" and obj.get("refusal") is True:
            return {"status": "VALID", "reason_code": "REFUSAL", "refusal": True}
        return {"status": "INVALID", "reason_code": "EMPTY", "refusal": False}
    except Exception:
        return {"status": "INVALID", "reason_code": "EMPTY", "refusal": False}


def _validation_ai_talk_description_refusal(client: OpenAI, user_text: str) -> Dict[str, Any]:
    """Detect skip/defer for optional talk description."""
    prompt = f"""
The user was asked to describe their talk or expertise. They replied: "{user_text}"

Decide if they are skipping or deferring (e.g. "skip", "none", "prefer not", "later", "I'll add later") or if they are trying to describe a talk.
- If they clearly skip or defer, return status "VALID" with "refusal": true.
- If they are describing (or attempting to describe) a talk or expertise, return status "INVALID" with reason_code "EMPTY".

Return JSON ONLY: {{ "status": "VALID" | "INVALID", "reason_code": "REFUSAL" | "EMPTY", "refusal": true only when status is VALID and they skipped }}
"""
    try:
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0,
            messages=[
                {"role": "system", "content": "Return ONLY JSON. Skip/defer → {\"status\":\"VALID\",\"reason_code\":\"REFUSAL\",\"refusal\":true}. Otherwise → {\"status\":\"INVALID\",\"reason_code\":\"EMPTY\"}."},
                {"role": "user", "content": prompt},
            ],
            timeout=10,
        )
        obj = _parse_validation_ai_json(completion.choices[0].message.content or "")
        if not obj:
            return {"status": "INVALID", "reason_code": "EMPTY", "refusal": False}
        if obj.get("status") == "VALID" and obj.get("refusal") is True:
            return {"status": "VALID", "reason_code": "REFUSAL", "refusal": True}
        return {"status": "INVALID", "reason_code": "EMPTY", "refusal": False}
    except Exception:
        return {"status": "INVALID", "reason_code": "EMPTY", "refusal": False}


def _validation_ai_talk_description_title_overview(client: OpenAI, user_text: str) -> Dict[str, Any]:
    """
    Decide if input is a real talk/expertise description vs random/off-topic; if valid, return title + overview for storage.
    normalized_value on VALID: {"title": str, "overview": str}
    """
    prompt = f"""
The user was asked to describe their talk or speaking expertise. They replied:

\"\"\"{user_text}\"\"\"

1) Is this a genuine description of a talk, workshop, keynote, session, or professional speaking expertise that an event organizer could use? Or is it random text, spam, jokes unrelated to speaking, off-topic rants, or keyboard gibberish?

2) If it is NOT a valid talk description, return JSON: {{"status":"INVALID","reason_code":"GIBBERISH"}} for nonsense/random characters, or {{"status":"INVALID","reason_code":"UNRELATED"}} for coherent but wrong-topic content.

3) If it IS valid, return JSON: {{"status":"VALID","reason_code":"OK","title":"<short title, max ~15 words>","overview":"<1-4 sentences capturing substance; use their content, do not invent credentials or claims not implied>"}}

Return JSON ONLY, no markdown.
"""
    try:
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0,
            messages=[
                {"role": "system", "content": "Return ONLY JSON. For VALID, both title and overview must be non-empty strings."},
                {"role": "user", "content": prompt},
            ],
            timeout=20,
        )
        obj = _parse_validation_ai_json(completion.choices[0].message.content or "")
        if not obj:
            return {"status": "INVALID", "reason_code": "AI_UNAVAILABLE", "normalized_value": None}
        if obj.get("status") == "INVALID":
            rc = obj.get("reason_code") or "UNRELATED"
            if rc not in ("GIBBERISH", "UNRELATED"):
                rc = "UNRELATED"
            return {"status": "INVALID", "reason_code": rc, "normalized_value": None}
        title = str(obj.get("title") or "").strip()
        overview = str(obj.get("overview") or "").strip()
        if not title or not overview:
            return {"status": "INVALID", "reason_code": "UNRELATED", "normalized_value": None}
        return {"status": "VALID", "reason_code": "OK", "normalized_value": {"title": title, "overview": overview}}
    except Exception:
        return {"status": "INVALID", "reason_code": "AI_UNAVAILABLE", "normalized_value": None}


def _validation_ai_key_takeaways_refusal(client: OpenAI, user_text: str) -> Dict[str, Any]:
    """Detect skip for optional key takeaways."""
    prompt = f"""
The user was asked what key takeaways they highlight from their talks. They replied: "{user_text}"

Decide if they are skipping (e.g. "skip", "none", "prefer not", "later", "no takeaways") or providing content about takeaways.
- If they clearly skip, return status "VALID" with "refusal": true.
- If they are providing takeaway-related content, return status "INVALID" with reason_code "EMPTY".

Return JSON ONLY: {{ "status": "VALID" | "INVALID", "reason_code": "REFUSAL" | "EMPTY", "refusal": true only when VALID and skipped }}
"""
    try:
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0,
            messages=[
                {"role": "system", "content": "Return ONLY JSON. Skip → {\"status\":\"VALID\",\"reason_code\":\"REFUSAL\",\"refusal\":true}. Else → {\"status\":\"INVALID\",\"reason_code\":\"EMPTY\"}."},
                {"role": "user", "content": prompt},
            ],
            timeout=10,
        )
        obj = _parse_validation_ai_json(completion.choices[0].message.content or "")
        if not obj:
            return {"status": "INVALID", "reason_code": "EMPTY", "refusal": False}
        if obj.get("status") == "VALID" and obj.get("refusal") is True:
            return {"status": "VALID", "reason_code": "REFUSAL", "refusal": True}
        return {"status": "INVALID", "reason_code": "EMPTY", "refusal": False}
    except Exception:
        return {"status": "INVALID", "reason_code": "EMPTY", "refusal": False}


def _validate_and_extract_testimonials(client: OpenAI, user_text: str) -> Dict[str, Any]:
    """Return VALID with normalized_value list of testimonial strings, or INVALID if not real testimonials."""
    prompt = f"""
The user was asked to share testimonials (quotes or feedback) from past speaking engagements. They replied:

\"\"\"{user_text}\"\"\"

1) Is this genuine testimonial-style content—praise, quotes, or feedback about the speaker's speaking—or is it random text, unrelated answers, jokes, or not testimonials?

2) If NOT genuine testimonials, return {{"status":"INVALID","reason_code":"UNRELATED"}} (or "GIBBERISH" for nonsense).

3) If genuine, split into separate strings: each item is one distinct testimonial quote (merge broken lines of the same quote into one string). At least one non-empty item.

Return JSON ONLY: {{"status":"VALID","reason_code":"OK","items":["..."]}} or INVALID as above.
"""
    try:
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0,
            messages=[
                {"role": "system", "content": "Return ONLY JSON. items must be a non-empty array of strings when status is VALID."},
                {"role": "user", "content": prompt},
            ],
            timeout=20,
        )
        obj = _parse_validation_ai_json(completion.choices[0].message.content or "")
        if not obj:
            return {"status": "INVALID", "reason_code": "AI_UNAVAILABLE", "normalized_value": None}
        if obj.get("status") == "INVALID":
            rc = obj.get("reason_code") or "UNRELATED"
            if rc not in ("GIBBERISH", "UNRELATED"):
                rc = "UNRELATED"
            return {"status": "INVALID", "reason_code": rc, "normalized_value": None}
        raw_items = obj.get("items")
        if not isinstance(raw_items, list):
            return {"status": "INVALID", "reason_code": "UNRELATED", "normalized_value": None}
        cleaned = [str(x).strip() for x in raw_items if str(x).strip()]
        if not cleaned:
            return {"status": "INVALID", "reason_code": "UNRELATED", "normalized_value": None}
        return {"status": "VALID", "reason_code": "OK", "normalized_value": cleaned}
    except Exception:
        return {"status": "INVALID", "reason_code": "AI_UNAVAILABLE", "normalized_value": None}


def _validate_and_extract_key_takeaways(client: OpenAI, user_text: str) -> Dict[str, Any]:
    """Return VALID with list of takeaway strings, or INVALID if not real key takeaways."""
    prompt = f"""
The user was asked what key takeaways audiences get from their talks. They replied:

\"\"\"{user_text}\"\"\"

1) Is this genuine key-takeaway content—concrete points, lessons, or outcomes audiences gain—or random text, unrelated content, or not about talk takeaways?

2) If NOT genuine key takeaways, return {{"status":"INVALID","reason_code":"UNRELATED"}} or {{"status":"INVALID","reason_code":"GIBBERISH"}}.

3) If genuine, return one string per distinct takeaway in "items" (short phrases or single sentences each).

Return JSON ONLY: {{"status":"VALID","reason_code":"OK","items":["..."]}} or INVALID as above.
"""
    try:
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0,
            messages=[
                {"role": "system", "content": "Return ONLY JSON. items must be non-empty when VALID."},
                {"role": "user", "content": prompt},
            ],
            timeout=20,
        )
        obj = _parse_validation_ai_json(completion.choices[0].message.content or "")
        if not obj:
            return {"status": "INVALID", "reason_code": "AI_UNAVAILABLE", "normalized_value": None}
        if obj.get("status") == "INVALID":
            rc = obj.get("reason_code") or "UNRELATED"
            if rc not in ("GIBBERISH", "UNRELATED"):
                rc = "UNRELATED"
            return {"status": "INVALID", "reason_code": rc, "normalized_value": None}
        raw_items = obj.get("items")
        if not isinstance(raw_items, list):
            return {"status": "INVALID", "reason_code": "UNRELATED", "normalized_value": None}
        cleaned = [str(x).strip() for x in raw_items if str(x).strip()]
        if not cleaned:
            return {"status": "INVALID", "reason_code": "UNRELATED", "normalized_value": None}
        return {"status": "VALID", "reason_code": "OK", "normalized_value": cleaned}
    except Exception:
        return {"status": "INVALID", "reason_code": "AI_UNAVAILABLE", "normalized_value": None}


def _extract_past_speaking_structured(client: OpenAI, user_text: str) -> List[Dict[str, Any]]:
    """Parse free text into list of dicts for past_speaking_examples (organization, optional event, date only)."""
    schema_hint = '{"entries": [{"organization_name": "", "event_name": "", "date_month_year": ""}]}'
    prompt = f"""Extract past speaking engagements from the user's text. For each engagement capture ONLY:
- organization_name: hosting organization, company, or venue name (required when inferable)
- event_name: conference or event name if they gave one (optional; empty string if unknown)
- date_month_year: when it happened, e.g. "March 2024", "Q1 2023", or "2022" if only year known

Do NOT ask for topics or audience in output—omit those concepts.

User text:
\"\"\"{user_text}\"\"\"

Return JSON ONLY with shape {schema_hint}. One object per distinct engagement. Use empty string for unknown optional fields. If nothing extractable, return {{"entries": []}}."""
    try:
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0,
            messages=[
                {"role": "system", "content": "Return ONLY valid JSON with an 'entries' array. No markdown."},
                {"role": "user", "content": prompt},
            ],
            timeout=20,
        )
        obj = _parse_validation_ai_json(completion.choices[0].message.content or "")
        if not obj or not isinstance(obj.get("entries"), list):
            return []
        out: List[Dict[str, Any]] = []
        for e in obj["entries"]:
            if not isinstance(e, dict):
                continue
            org = str(e.get("organization_name") or "").strip()
            ev = str(e.get("event_name") or "").strip()
            dt = str(e.get("date_month_year") or e.get("date") or "").strip()
            row = {"organization_name": org, "event_name": ev, "date_month_year": dt}
            if org or ev or dt:
                out.append(row)
        return out
    except Exception:
        return []


def _validation_ai_full_name_check(client: OpenAI, name: str) -> Dict[str, Any]:
    """Return INVALID if name looks like gibberish/random. Both first and last must look like real names."""
    prompt = f"""
The user was asked for their full name. They entered: "{name}"

Both the first name AND the last name (or every part of the name) must look like a real person's name.
- If the full name looks real (e.g. "John Smith", "Marie-Claire O'Brien", "Akash Kumar"), return VALID.
- If any part is clearly not a real name—random letters, keyboard smash, gibberish (e.g. "Akash ksdgjsdgrwgb", "John xyzabc", "asdf qwerty")—return INVALID. The last name must be a plausible real name, not random characters.

Return JSON ONLY: {{ "status": "VALID" | "INVALID", "reason_code": "INVALID_FULL_NAME" if INVALID else "OK" }}
"""
    try:
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0,
            messages=[
                {"role": "system", "content": "Return ONLY JSON. One of: {\"status\": \"VALID\", \"reason_code\": \"OK\"} or {\"status\": \"INVALID\", \"reason_code\": \"INVALID_FULL_NAME\"}"},
                {"role": "user", "content": prompt},
            ],
            timeout=10,
        )
        obj = _parse_validation_ai_json(completion.choices[0].message.content or "")
        if not obj:
            return {"status": "INVALID", "reason_code": "INVALID_FULL_NAME", "normalized_value": None}
        status = obj.get("status")
        if status == "VALID":
            return {"status": "VALID", "reason_code": "OK", "normalized_value": None}
        return {"status": "INVALID", "reason_code": "INVALID_FULL_NAME", "normalized_value": None}
    except Exception:
        return {"status": "INVALID", "reason_code": "INVALID_FULL_NAME", "normalized_value": None}


def _validation_ai_enum_intent(
    client: OpenAI,
    step: StepDefinition,
    user_text: str,
) -> Dict[str, Any]:
    """Map free-text user input to allowed enum values. Reject only empty, gibberish, spam, unrelated."""
    allowed = step.allowed_values or []
    if not allowed:
        return {"status": "INVALID", "reason_code": "ENUM_INVALID", "normalized_value": None}
    allowed_reason_codes = ["EMPTY", "GIBBERISH", "SPAM", "UNRELATED"]
    prompt = f"""
You are ValidationAI for a single-choice/multi-choice field. Map the user's intent to the allowed options.
Question: {step.question}
Allowed values (return only these when VALID): {json.dumps(allowed)}
User response: {user_text}

Focus on intent only. Accept vague, long, or slightly off answers; normalize to one or more allowed values.
Return INVALID only for: empty, gibberish, spam, or completely unrelated input.
Return JSON ONLY: {{ "status": "VALID" | "INVALID", "reason_code": one of {allowed_reason_codes}, "normalized_value": array of strings from allowed values, or null }}
"""
    try:
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0,
            messages=[
                {"role": "system", "content": "Return ONLY JSON. normalized_value must be a subset of the allowed values when VALID."},
                {"role": "user", "content": prompt},
            ],
            timeout=10,
        )
        obj = _parse_validation_ai_json(completion.choices[0].message.content or "")
        if not obj:
            return {"status": "INVALID", "reason_code": "AI_UNAVAILABLE", "normalized_value": None}
        status = obj.get("status")
        reason_code = obj.get("reason_code") or "UNRELATED"
        normalized_value = obj.get("normalized_value")
        if status == "VALID" and isinstance(normalized_value, list):
            allowed_set = {v.strip().lower(): v for v in allowed}
            cleaned = []
            for x in normalized_value:
                key = str(x).strip().lower()
                if key in allowed_set:
                    cleaned.append(allowed_set[key])
            if cleaned:
                return {"status": "VALID", "reason_code": "OK", "normalized_value": list(dict.fromkeys(cleaned))}
        return {"status": "INVALID", "reason_code": reason_code if reason_code in allowed_reason_codes else "UNRELATED", "normalized_value": None}
    except Exception:
        return {"status": "INVALID", "reason_code": "AI_UNAVAILABLE", "normalized_value": None}


def _validation_ai_enum_intent_topics(
    client: OpenAI,
    step: StepDefinition,
    user_text: str,
    allowed_topics: List[dict],
) -> Dict[str, Any]:
    """Map free-text user input to allowed topics (by name). Returns list of full topic objects."""
    if not allowed_topics:
        return {"status": "INVALID", "reason_code": "ENUM_INVALID", "normalized_value": None}
    allowed_names = [t.get("name", "").strip() for t in allowed_topics if t.get("name")]
    name_to_topic = {t.get("name", "").strip().lower(): t for t in allowed_topics if t.get("name")}
    if not name_to_topic:
        return {"status": "INVALID", "reason_code": "ENUM_INVALID", "normalized_value": None}
    allowed_reason_codes = ["EMPTY", "GIBBERISH", "SPAM", "UNRELATED"]
    prompt = f"""
You are ValidationAI for a multi-choice field. Map the user's intent to the allowed topic options.
Question: {step.question}
Allowed values (return only these names when VALID): {json.dumps(allowed_names)}
User response: {user_text}

Focus on intent only. Accept vague, long, or slightly off answers; normalize to one or more allowed topic names.
Return INVALID only for: empty, gibberish, spam, or completely unrelated input.
Return JSON ONLY: {{ "status": "VALID" | "INVALID", "reason_code": one of {allowed_reason_codes}, "normalized_value": array of topic names from allowed list, or null }}
"""
    try:
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0,
            messages=[
                {"role": "system", "content": "Return ONLY JSON. normalized_value must be a subset of the allowed topic names when VALID."},
                {"role": "user", "content": prompt},
            ],
            timeout=10,
        )
        obj = _parse_validation_ai_json(completion.choices[0].message.content or "")
        if not obj:
            return {"status": "INVALID", "reason_code": "AI_UNAVAILABLE", "normalized_value": None}
        status = obj.get("status")
        reason_code = obj.get("reason_code") or "UNRELATED"
        normalized_value = obj.get("normalized_value")
        if status == "VALID" and isinstance(normalized_value, list):
            seen = set()
            out = []
            for x in normalized_value:
                key = str(x).strip().lower()
                if key in name_to_topic and key not in seen:
                    seen.add(key)
                    out.append(name_to_topic[key])
            if out:
                return {"status": "VALID", "reason_code": "OK", "normalized_value": out}
        return {"status": "INVALID", "reason_code": reason_code if reason_code in allowed_reason_codes else "UNRELATED", "normalized_value": None}
    except Exception:
        return {"status": "INVALID", "reason_code": "AI_UNAVAILABLE", "normalized_value": None}


def _validation_ai_enum_intent_target_audiences(
    client: OpenAI,
    step: StepDefinition,
    user_text: str,
    allowed_audiences: List[dict],
) -> Dict[str, Any]:
    """Map free-text user input to allowed target audiences (by name). Returns list of full audience objects."""
    if not allowed_audiences:
        return {"status": "INVALID", "reason_code": "ENUM_INVALID", "normalized_value": None}
    allowed_names = [a.get("name", "").strip() for a in allowed_audiences if a.get("name")]
    name_to_audience = {a.get("name", "").strip().lower(): a for a in allowed_audiences if a.get("name")}
    if not name_to_audience:
        return {"status": "INVALID", "reason_code": "ENUM_INVALID", "normalized_value": None}
    allowed_reason_codes = ["EMPTY", "GIBBERISH", "SPAM", "UNRELATED"]
    prompt = f"""
You are ValidationAI for a multi-choice field. Map the user's intent to the allowed target audience options.
Question: {step.question}
Allowed values (return only these names when VALID): {json.dumps(allowed_names)}
User response: {user_text}

Focus on intent only. Accept vague, long, or slightly off answers; normalize to one or more allowed audience names.
Return INVALID only for: empty, gibberish, spam, or completely unrelated input.
Return JSON ONLY: {{ "status": "VALID" | "INVALID", "reason_code": one of {allowed_reason_codes}, "normalized_value": array of audience names from allowed list, or null }}
"""
    try:
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0,
            messages=[
                {"role": "system", "content": "Return ONLY JSON. normalized_value must be a subset of the allowed audience names when VALID."},
                {"role": "user", "content": prompt},
            ],
            timeout=10,
        )
        obj = _parse_validation_ai_json(completion.choices[0].message.content or "")
        if not obj:
            return {"status": "INVALID", "reason_code": "AI_UNAVAILABLE", "normalized_value": None}
        status = obj.get("status")
        reason_code = obj.get("reason_code") or "UNRELATED"
        normalized_value = obj.get("normalized_value")
        if status == "VALID" and isinstance(normalized_value, list):
            seen = set()
            out = []
            for x in normalized_value:
                key = str(x).strip().lower()
                if key in name_to_audience and key not in seen:
                    seen.add(key)
                    out.append(name_to_audience[key])
            if out:
                return {"status": "VALID", "reason_code": "OK", "normalized_value": out}
        return {"status": "INVALID", "reason_code": reason_code if reason_code in allowed_reason_codes else "UNRELATED", "normalized_value": None}
    except Exception:
        return {"status": "INVALID", "reason_code": "AI_UNAVAILABLE", "normalized_value": None}


def validate_step(
    step_name: str,
    answer: Union[str, List[str], List[dict]],
    source: str,
    expected_step_name: Optional[str] = None,
    profile_context: Optional[Dict[str, Any]] = None,
    allowed_topics_for_step: Optional[List[dict]] = None,
    allowed_target_audiences_for_step: Optional[List[dict]] = None,
) -> Dict[str, Any]:
    step = get_step_by_name(step_name)
    if not step:
        return {"status": "INVALID", "reason_code": "UNKNOWN_STEP", "normalized_value": None}
    if expected_step_name and step_name != expected_step_name:
        return {"status": "INVALID", "reason_code": "OUT_OF_ORDER", "normalized_value": None}

    normalized, err = _validate_basic(step, answer)
    if err:
        return {"status": "INVALID", "reason_code": _reason_code_from_basic_error(err), "normalized_value": None}

    api_key = os.getenv("OPENAI_API_KEY")
    client = OpenAI(api_key=api_key) if api_key else None

    # code_full_name: extract name from free-form text (e.g. "My name is Manish Gautam") then format + gibberish check
    if step.validation_mode == "code_full_name":
        text = normalized if isinstance(normalized, str) else " ".join(normalized)
        if client and text.strip():
            extract_res = _validation_ai_full_name_extract(client, text)
            if extract_res.get("status") == "VALID" and extract_res.get("normalized_value"):
                text = extract_res["normalized_value"]
            else:
                # Surface REFUSAL or INVALID_FULL_NAME from extract so recovery can show calm tone when user declined
                return {"status": "INVALID", "reason_code": extract_res.get("reason_code") or "INVALID_FULL_NAME", "normalized_value": None}
        if not validate_full_name(text):
            return {"status": "INVALID", "reason_code": "INVALID_FULL_NAME", "normalized_value": None}
        if client:
            ai_res = _validation_ai_full_name_check(client, text)
            if ai_res.get("status") == "INVALID":
                return {"status": "INVALID", "reason_code": "INVALID_FULL_NAME", "normalized_value": None}
        return {"status": "VALID", "reason_code": "OK", "normalized_value": _normalize_name_for_validation(text)}

    # email_regex: AI extracts email from text; we verify format with regex. Surface REFUSAL for calm recovery message.
    if step.validation_mode == "email_regex":
        text = (normalized if isinstance(normalized, str) else " ".join(str(x).strip() for x in normalized if str(x).strip())).strip()
        if not text:
            return {"status": "INVALID", "reason_code": "INVALID_EMAIL", "normalized_value": None}
        if client:
            ai_res = _validation_ai_email_extract(client, text)
            if ai_res.get("status") == "VALID" and ai_res.get("normalized_value"):
                extracted = ai_res["normalized_value"]
                if validate_email(extracted):
                    return {"status": "VALID", "reason_code": "OK", "normalized_value": extracted.strip().lower()}
            else:
                return {"status": "INVALID", "reason_code": ai_res.get("reason_code") or "INVALID_EMAIL", "normalized_value": None}
        if validate_email(text):
            return {"status": "VALID", "reason_code": "OK", "normalized_value": text.strip().lower()}
        return {"status": "INVALID", "reason_code": "INVALID_EMAIL", "normalized_value": None}

    # linkedin_url step: professional social URLs (LinkedIn, Facebook, X, Instagram); optional skip / defer to profile.
    if step.step_name == "linkedin_url" and step.validation_mode == "social_media_urls":
        text = (normalized if isinstance(normalized, str) else "").strip()
        if not text:
            return {"status": "VALID", "reason_code": "OK", "normalized_value": None}
        parsed = _parse_professional_social_urls(text)
        if parsed:
            return {"status": "VALID", "reason_code": "OK", "normalized_value": parsed}
        # HTTP present but no recognized professional social URL
        if _SOCIAL_URL_SCRAPE_PATTERN.search(text):
            return {"status": "INVALID", "reason_code": "INVALID_URL", "normalized_value": None}
        defer_phrases = [
            "update my profile", "update the profile", "update profile", "add later", "add it later",
            "do it later", "later", "profile settings", "edit profile", "skip", "prefer not", "none",
            "don't have", "do not have", "rather not", "not now", "another time",
        ]
        tl = text.lower()
        if any(p in tl for p in defer_phrases):
            return {"status": "VALID", "reason_code": "OK", "normalized_value": None}
        if client:
            ai_res = _validation_ai_linkedin_refusal(client, text)
            if ai_res.get("refusal") is True:
                return {"status": "VALID", "reason_code": "OK", "normalized_value": None}
        refusal_phrases = [
            "don't want", "no linkedin", "don't use linkedin", "not on linkedin", "no profile",
            "no social", "no urls", "no url",
        ]
        if any(p in tl for p in refusal_phrases):
            return {"status": "VALID", "reason_code": "OK", "normalized_value": None}
        return {"status": "INVALID", "reason_code": "INVALID_URL", "normalized_value": None}

    # topics_multiselect: allowed values from DB (allowed_topics_for_step)
    if step.validation_mode == "topics_multiselect" and allowed_topics_for_step is not None:
        if source == "selection":
            # answer is list of topic objects; validate by _id/slug and return full topic objects
            if not isinstance(normalized, list) or not normalized:
                return {"status": "INVALID", "reason_code": "EMPTY", "normalized_value": None}
            by_id = {str(t.get("_id", "")).strip(): t for t in allowed_topics_for_step}
            by_slug = {str(t.get("slug", "")).strip().lower(): t for t in allowed_topics_for_step}
            out = []
            for sel in normalized:
                if not isinstance(sel, dict):
                    continue
                sid = str(sel.get("_id", "")).strip()
                sslug = str(sel.get("slug", "")).strip().lower()
                if sid and sid in by_id:
                    out.append(by_id[sid])
                elif sslug and sslug in by_slug:
                    out.append(by_slug[sslug])
            if not out:
                return {"status": "INVALID", "reason_code": "ENUM_INVALID", "normalized_value": None}
            # Dedupe by _id while preserving order
            seen_ids = set()
            deduped = []
            for t in out:
                tid = t.get("_id")
                if tid not in seen_ids:
                    seen_ids.add(tid)
                    deduped.append(t)
            return {"status": "VALID", "reason_code": "OK", "normalized_value": deduped}
        else:
            # source == "text": AI maps user text to topic names, then we map to full topic objects
            text = normalized if isinstance(normalized, str) else " ".join(str(x).strip() for x in normalized if str(x).strip())
            if not text.strip():
                return {"status": "INVALID", "reason_code": "EMPTY", "normalized_value": None}
            if not client:
                return {"status": "INVALID", "reason_code": "AI_UNAVAILABLE", "normalized_value": None}
            ai_res = _validation_ai_enum_intent_topics(client, step, text, allowed_topics_for_step)
            return ai_res

    # target_audiences_multiselect: allowed values from DB (allowed_target_audiences_for_step)
    if step.validation_mode == "target_audiences_multiselect" and allowed_target_audiences_for_step is not None:
        if source == "selection":
            if not isinstance(normalized, list) or not normalized:
                return {"status": "INVALID", "reason_code": "EMPTY", "normalized_value": None}
            by_id = {str(t.get("_id", "")).strip(): t for t in allowed_target_audiences_for_step}
            by_slug = {str(t.get("slug", "")).strip().lower(): t for t in allowed_target_audiences_for_step}
            out = []
            for sel in normalized:
                if not isinstance(sel, dict):
                    continue
                sid = str(sel.get("_id", "")).strip()
                sslug = str(sel.get("slug", "")).strip().lower()
                if sid and sid in by_id:
                    out.append(by_id[sid])
                elif sslug and sslug in by_slug:
                    out.append(by_slug[sslug])
            if not out:
                return {"status": "INVALID", "reason_code": "ENUM_INVALID", "normalized_value": None}
            seen_ids = set()
            deduped = []
            for t in out:
                tid = t.get("_id")
                if tid not in seen_ids:
                    seen_ids.add(tid)
                    deduped.append(t)
            return {"status": "VALID", "reason_code": "OK", "normalized_value": deduped}
        else:
            text = normalized if isinstance(normalized, str) else " ".join(str(x).strip() for x in normalized if str(x).strip())
            if not text.strip():
                return {"status": "INVALID", "reason_code": "EMPTY", "normalized_value": None}
            if not client:
                return {"status": "INVALID", "reason_code": "AI_UNAVAILABLE", "normalized_value": None}
            ai_res = _validation_ai_enum_intent_target_audiences(client, step, text, allowed_target_audiences_for_step)
            return ai_res

    # video_links is optional: empty or refusal -> skip (VALID None); still validate YouTube/Vimeo when they provide URLs
    if step.step_name == "video_links" and step.validation_mode == "url_only":
        text = (
            normalized[0] if isinstance(normalized, list) and len(normalized) == 1 and isinstance(normalized[0], str)
            else (normalized if isinstance(normalized, str) else " ".join(str(x).strip() for x in normalized if str(x).strip()))
        )
        if isinstance(text, list):
            text = " ".join(str(x).strip() for x in text if str(x).strip())
        text = (text or "").strip()
        if not text:
            return {"status": "VALID", "reason_code": "OK", "normalized_value": None}
        # If it looks like URL(s), let _validate_rule_based validate; otherwise check refusal
        if "http" in text.lower() or "youtube" in text.lower() or "youtu.be" in text.lower():
            pass  # fall through to _validate_rule_based
        else:
            # Reject gibberish/random text (same idea as linkedin_url): code check first, then AI
            if _check_gibberish(text):
                return {"status": "INVALID", "reason_code": "GIBBERISH", "normalized_value": None}
            refusal_phrases = [
                "don't have", "don't have any", "do not have", "prefer not", "skip", "none", "no videos",
                "don't want to share", "rather not", "nothing to share", "no links", "no video",
                "not yet", "don't have any yet", "no speaking videos",
            ]
            if any(p in text.lower() for p in refusal_phrases):
                return {"status": "VALID", "reason_code": "OK", "normalized_value": None}
            if client:
                ai_gibberish = _validation_ai_gibberish_check(client, step, text)
                if ai_gibberish.get("status") == "INVALID":
                    return {"status": "INVALID", "reason_code": "GIBBERISH", "normalized_value": None}
                ai_res = _validation_ai_video_links_refusal(client, text)
                if ai_res.get("refusal") is True:
                    return {"status": "VALID", "reason_code": "OK", "normalized_value": None}
            return {"status": "INVALID", "reason_code": "INVALID_URL", "normalized_value": None}

    normalized, err = _validate_rule_based(step, normalized, source)
    if err:
        if step.validation_mode == "strict_enum_code" and source == "text" and step.allowed_values:
            # Pass user's raw answer text directly to AI for intent matching
            text = answer.strip() if isinstance(answer, str) else " ".join(str(x).strip() for x in answer if str(x).strip())
            if text.strip():
                api_key = os.getenv("OPENAI_API_KEY")
                client = OpenAI(api_key=api_key) if api_key else None
                if client:
                    ai_res = _validation_ai_enum_intent(client, step, text)
                    if ai_res.get("status") == "VALID":
                        return ai_res
                    return {"status": "INVALID", "reason_code": ai_res.get("reason_code") or "ENUM_INVALID", "normalized_value": None}
        return {"status": "INVALID", "reason_code": _reason_code_from_rule_error(err), "normalized_value": None}

    # textarea_accept: code gibberish check + AI gibberish check when available; then accept
    if step.validation_mode == "textarea_accept":
        text = normalized if isinstance(normalized, str) else " ".join(str(x).strip() for x in normalized if str(x).strip())
        if step.required and not text.strip():
            return {"status": "INVALID", "reason_code": "EMPTY", "normalized_value": None}
        # past_speaking_examples is optional: empty or refusal -> skip (VALID None); still validate when they provide content
        if step.step_name == "past_speaking_examples":
            if not text.strip():
                return {"status": "VALID", "reason_code": "OK", "normalized_value": None}
            refusal_phrases = [
                "don't have", "don't have any", "do not have", "prefer not", "skip", "none", "no examples",
                "don't want to share", "rather not", "nothing to share", "no past", "haven't done",
                "not yet", "no speaking", "don't have any yet",
            ]
            if any(p in text.lower() for p in refusal_phrases):
                return {"status": "VALID", "reason_code": "OK", "normalized_value": None}
            if client:
                ai_res = _validation_ai_past_speaking_refusal(client, text)
                if ai_res.get("refusal") is True:
                    return {"status": "VALID", "reason_code": "OK", "normalized_value": None}
        if _check_gibberish(text):
            return {"status": "INVALID", "reason_code": "GIBBERISH", "normalized_value": None}
        if client:
            ai_res = _validation_ai_gibberish_check(client, step, text)
            if ai_res.get("status") == "INVALID":
                return {"status": "INVALID", "reason_code": "GIBBERISH", "normalized_value": None}
        if step.step_name == "past_speaking_examples":
            if client:
                extracted = _extract_past_speaking_structured(client, text)
                if extracted:
                    return {"status": "VALID", "reason_code": "OK", "normalized_value": extracted}
            return {
                "status": "VALID",
                "reason_code": "OK",
                "normalized_value": [{
                    "organization_name": text.strip(),
                    "event_name": "",
                    "date_month_year": "",
                }],
            }
        if step.split_on_conjunctions:
            parts = split_input_topics(text)
            text = ", ".join(parts) if parts else text
        return {"status": "VALID", "reason_code": "OK", "normalized_value": text}

    # No AI available for AI-backed step: accept input and continue (AI is helper, not gatekeeper)
    if step.uses_ai and not client:
        return {"status": "VALID", "reason_code": "OK", "normalized_value": normalized}

    try:
        if step.validation_mode == "semantic_text_ai":
            text = normalized if isinstance(normalized, str) else " ".join(str(x) for x in normalized if x)

            # talk_description: optional skip; LLM checks real description vs random; saves {title, overview}
            if step.step_name == "talk_description":
                if not text.strip():
                    return {"status": "VALID", "reason_code": "OK", "normalized_value": None}
                td_refusal_phrases = [
                    "don't want to share", "prefer not", "skip", "none", "later", "not now",
                    "i'll add", "ill add", "add later", "pass", "no thanks", "rather not",
                ]
                if any(p in text.lower() for p in td_refusal_phrases):
                    return {"status": "VALID", "reason_code": "OK", "normalized_value": None}
                if client:
                    ref_td = _validation_ai_talk_description_refusal(client, text)
                    if ref_td.get("refusal") is True:
                        return {"status": "VALID", "reason_code": "OK", "normalized_value": None}
                if _check_gibberish(text):
                    return {"status": "INVALID", "reason_code": "GIBBERISH", "normalized_value": None}
                if not client:
                    words = text.strip().split()
                    short_title = " ".join(words[:12]) if words else text.strip()
                    return {
                        "status": "VALID",
                        "reason_code": "OK",
                        "normalized_value": {"title": short_title[:200], "overview": text.strip()[:2000]},
                    }
                td_res = _validation_ai_talk_description_title_overview(client, text)
                if td_res.get("status") == "VALID" and isinstance(td_res.get("normalized_value"), dict):
                    return {
                        "status": "VALID",
                        "reason_code": "OK",
                        "normalized_value": td_res["normalized_value"],
                    }
                return {
                    "status": "INVALID",
                    "reason_code": td_res.get("reason_code") or "UNRELATED",
                    "normalized_value": None,
                }

            # key_takeaways: optional skip; LLM validates and returns list of strings
            if step.step_name == "key_takeaways":
                if not text.strip():
                    return {"status": "VALID", "reason_code": "OK", "normalized_value": None}
                kt_refusal_phrases = [
                    "don't have", "don't have any", "do not have", "prefer not", "skip", "none",
                    "don't want to share", "rather not", "nothing to share", "not yet", "later", "pass",
                    "no takeaways", "no key takeaways",
                ]
                if any(p in text.lower() for p in kt_refusal_phrases):
                    return {"status": "VALID", "reason_code": "OK", "normalized_value": None}
                if client:
                    ref_kt = _validation_ai_key_takeaways_refusal(client, text)
                    if ref_kt.get("refusal") is True:
                        return {"status": "VALID", "reason_code": "OK", "normalized_value": None}
                if _check_gibberish(text):
                    return {"status": "INVALID", "reason_code": "GIBBERISH", "normalized_value": None}
                if not client:
                    parts = [p.strip() for p in re.split(r"[\n•;]+|\s*[,;]\s*", text) if p.strip()]
                    items = parts if len(parts) > 1 else [text.strip()]
                    return {"status": "VALID", "reason_code": "OK", "normalized_value": items}
                kt_res = _validate_and_extract_key_takeaways(client, text)
                if kt_res.get("status") == "VALID" and isinstance(kt_res.get("normalized_value"), list):
                    return {
                        "status": "VALID",
                        "reason_code": "OK",
                        "normalized_value": kt_res["normalized_value"],
                    }
                return {
                    "status": "INVALID",
                    "reason_code": kt_res.get("reason_code") or "UNRELATED",
                    "normalized_value": None,
                }

            # testimonial: optional skip; LLM validates real testimonials and returns list of strings
            if step.step_name == "testimonial":
                if not text.strip():
                    return {"status": "VALID", "reason_code": "OK", "normalized_value": None}
                refusal_phrases = [
                    "don't have", "don't have any", "do not have", "prefer not", "skip", "none", "no testimonials",
                    "don't want to share", "rather not", "nothing to share", "not yet",
                    "don't have any yet", "cannot share", "can not share", "i cannot", "no i cannot",
                    "won't share", "will not share", "no testimonial",
                ]
                if any(p in text.lower() for p in refusal_phrases):
                    return {"status": "VALID", "reason_code": "OK", "normalized_value": None}
                if client:
                    ai_res = _validation_ai_testimonial_refusal(client, text)
                    if ai_res.get("refusal") is True:
                        return {"status": "VALID", "reason_code": "OK", "normalized_value": None}
                if _check_gibberish(text):
                    return {"status": "INVALID", "reason_code": "GIBBERISH", "normalized_value": None}
                if not client:
                    return {"status": "VALID", "reason_code": "OK", "normalized_value": [text.strip()]}
                tm_res = _validate_and_extract_testimonials(client, text)
                if tm_res.get("status") == "VALID" and isinstance(tm_res.get("normalized_value"), list):
                    return {
                        "status": "VALID",
                        "reason_code": "OK",
                        "normalized_value": tm_res["normalized_value"],
                    }
                return {
                    "status": "INVALID",
                    "reason_code": tm_res.get("reason_code") or "UNRELATED",
                    "normalized_value": None,
                }

            # Any other semantic_text_ai step (future): optional empty + intent
            if not step.required and not text.strip():
                out = [] if step.validation_type in ("array_of_strings", "array_of_urls") else ""
                return {"status": "VALID", "reason_code": "OK", "normalized_value": out}
            if _check_gibberish(text):
                return {"status": "INVALID", "reason_code": "GIBBERISH", "normalized_value": None}
            if not client:
                return {"status": "VALID", "reason_code": "OK", "normalized_value": normalized}
            try:
                ai = _validation_ai_intent(client, step, text, profile_context=profile_context)
                if ai.get("reason_code") == "AI_UNAVAILABLE":
                    return {"status": "VALID", "reason_code": "OK", "normalized_value": normalized}
                if ai.get("status") == "VALID":
                    return {"status": "VALID", "reason_code": "OK", "normalized_value": normalized}
                reason_code = ai.get("reason_code") or "UNRELATED"
                if reason_code in SEMANTIC_OVERRIDE_REASON_CODES:
                    return {"status": "VALID", "reason_code": "OK", "normalized_value": normalized}
                return {"status": "INVALID", "reason_code": reason_code, "normalized_value": None}
            except Exception:
                return {"status": "VALID", "reason_code": "OK", "normalized_value": normalized}
    except Exception:
        return {"status": "VALID", "reason_code": "OK", "normalized_value": normalized}

    return {"status": "VALID", "reason_code": "OK", "normalized_value": normalized}


def get_init_response() -> dict:
    first = get_first_step()
    payload = step_to_response(first)
    # generate_welcome_message commented out — static fallback (same as former AI fallback)
    welcome_message = (
        "Hello! Thanks for joining Human Driven AI! My job is to find the right speaking opportunities for you "
        "including drafting the submission materials for each event.<br>Let's begin with the basics. What is your name?"
    )
    payload["question"] = welcome_message
    payload["assistant_message"] = welcome_message
    return payload


def get_expected_next_step(current_step_name: Optional[str] = None) -> Optional[str]:
    if current_step_name is None:
        return STEPS[0].step_name if STEPS else None
    for i, s in enumerate(STEPS):
        if s.step_name == current_step_name:
            if i + 1 < len(STEPS):
                return STEPS[i + 1].step_name
            return None
    return None


def validate_full_profile(
    profile_data: dict,
    allowed_topics: Optional[List[dict]] = None,
    allowed_target_audiences: Optional[List[dict]] = None,
) -> List[str]:
    errors = []
    field_mapping = {
        "full_name": ("full_name", "text"),
        "email": ("email", "text"),
        "topics": ("topics", "selection"),
        "speaking_formats": ("speaking_formats", "selection"),
        "delivery_mode": ("delivery_mode", "selection"),
        "linkedin_url": ("linkedin_url", "text"),
        "past_speaking_examples": ("past_speaking_examples", "text"),
        "video_links": ("video_links", "text"),
        "talk_description": ("talk_description", "text"),
        "key_takeaways": ("key_takeaways", "text"),
        "testimonial": ("testimonial", "text"),
        "target_audiences": ("target_audiences", "selection"),
    }
    for step_def in STEPS:
        field_name = step_def.step_name
        if field_name not in field_mapping:
            continue
        value_key, default_source = field_mapping[field_name]
        value = profile_data.get(value_key)
        if field_name == "talk_description" and isinstance(value, dict):
            value = f"{value.get('title', '')} {value.get('overview', '')}".strip()
        if field_name in ("key_takeaways", "testimonial") and isinstance(value, list):
            value = "\n".join(str(x).strip() for x in value if str(x).strip())
        if field_name == "linkedin_url":
            bits = []
            for k in ("linkedin_url", "facebook", "twitter", "instagram"):
                v = profile_data.get(k)
                if v:
                    bits.append(str(v).strip())
            value = " ".join(bits) if bits else value
        if not step_def.required and (value is None or value == [] or value == ""):
            continue
        # past_speaking_examples: list of dicts or legacy strings; flatten to text for validate_step
        if field_name == "past_speaking_examples" and isinstance(value, list):
            parts = []
            for v in value:
                if isinstance(v, dict):
                    parts.append(
                        " ".join(
                            str(v.get(k) or "").strip()
                            for k in ("organization_name", "event_name", "date_month_year")
                        ).strip()
                    )
                else:
                    parts.append(str(v).strip())
            value = " ".join(p for p in parts if p) if parts else ""
        source = default_source
        if step_def.validation_mode == "topics_multiselect":
            source = "selection" if isinstance(value, list) and value and isinstance(value[0], dict) else "text"
        elif step_def.validation_mode == "target_audiences_multiselect":
            source = "selection" if isinstance(value, list) and value and isinstance(value[0], dict) else "text"
        elif step_def.validation_mode == "strict_enum_code" and step_def.allowed_values:
            if isinstance(value, list):
                all_in_allowed = all(
                    str(v).strip().lower() in [a.lower() for a in step_def.allowed_values]
                    for v in value if v
                )
                source = "selection" if all_in_allowed else "text"
            elif isinstance(value, str):
                source = "selection" if value.strip().lower() in [a.lower() for a in step_def.allowed_values] else "text"
        res = validate_step(
            step_name=field_name,
            answer=value,
            source=source,
            profile_context=profile_data,
            allowed_topics_for_step=allowed_topics if field_name == "topics" else None,
            allowed_target_audiences_for_step=allowed_target_audiences if field_name == "target_audiences" else None,
        )
        if res.get("status") != "VALID":
            errors.append(field_name)
    return errors
