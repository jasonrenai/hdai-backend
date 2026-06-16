"""
Ordered onboarding script for the speaker profile chatbot.

The server picks the current step from the profile + session; the LLM asks one question,
calls upsert_speaker_profile for that step's fields, then advances.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple

# --- Step ids ---
CREATE_STEP = "create_profile"

POST_CREATE_STEP_ORDER: List[str] = [
    "location",
    "social",
    "bio",
    "professional_memberships",
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
]

CATALOG_STEPS = frozenset({"topics", "speaking_formats", "delivery_mode", "target_audiences"})

SKIPPABLE_STEPS = frozenset({
    "social",
    "professional_memberships",
    "past_speaking_examples",
    "video_links",
    "testimonial",
})

_PREFERRED_SPEAKING_TIMES = ["10-minute", "20-minute", "30-minute", "40-minute", "1 hour"]

# Verbatim / prescribed user-facing questions (keep in sync with chatbot service copy)
_QUESTION_LOCATION = (
    "What city, state or province, and country are you based in? "
    "You can answer in one line (e.g. Austin, Texas, United States)."
)
_QUESTION_SOCIAL = (
    "Share your primary, professional social media channel URLs "
    "(e.g., LinkedIn, Facebook, X, Instagram, etc.)."
)
_QUESTION_BIO = "Please share your professional bio in 50 - 100 words."
_QUESTION_MEMBERSHIPS = (
    "Please share your Professional Memberships, (e.g. Role, Organisation and topics)."
)
_QUESTION_SPEAKING_TIME = (
    "What is your preferred speaking time? You can choose one or more from the list below:<br><br>"
    "• 10-minute<br>• 20-minute<br>• 30-minute<br>• 40-minute<br>• 1 hour"
)
_QUESTION_TOPICS = "What are some of the topics you want to cover in your speaking opportunities?"
_QUESTION_FORMATS = "What speaking formats do you offer?"
_QUESTION_DELIVERY = "Do you want virtual events, in-person, hybrid, or a combination?"
_QUESTION_AUDIENCES = "Who are your target audiences?"
_QUESTION_TALK = (
    "Please provide a description of your talk, including the title and overview."
)
_QUESTION_TAKEAWAYS = "What 3 – 5 key takeaways would you like to highlight from your talk?"
_QUESTION_PAST_SPEAKING = (
    "Do you have past speaking examples you'd like to share? Please include the organization "
    "or event name and the corresponding date (month/year)."
)
_QUESTION_VIDEO = "Please share a YouTube video link of you speaking, or say skip if you have none."
_QUESTION_TESTIMONIAL = (
    "Do you have any testimonials or feedback from past speaking you'd like to share?"
)

STEP_UPSERT_FIELDS: Dict[str, Set[str]] = {
    CREATE_STEP: {
        "full_name",
        "professional_title",
        "company",
        "email",
        "phone_number",
    },
    "location": {"address_city", "address_state", "address_country"},
    "social": {"linkedin_url", "twitter", "facebook", "instagram"},
    "bio": {"bio"},
    "professional_memberships": {"professional_memberships"},
    "preferred_speaking_time": {"preferred_speaking_time"},
    "topics": {"topics"},
    "speaking_formats": {"speaking_formats"},
    "delivery_mode": {"delivery_mode"},
    "target_audiences": {"target_audiences"},
    "talk_description": {"talk_description"},
    "key_takeaways": {"key_takeaways"},
    "past_speaking_examples": {"past_speaking_examples"},
    "video_links": {"video_links"},
    "testimonial": {"testimonial"},
}

# Per-step hints for the model (acknowledgment + extraction); not shown verbatim to the user.
STEP_GUIDELINES: Dict[str, str] = {
    CREATE_STEP: (
        "Before profile exists: collect full_name, professional_title, company in chat; then ask for email and phone. "
        "Call upsert_speaker_profile once with all five fields when email and phone are ready."
    ),
    "location": "Parse city, state/province, country from one line; upsert all three address fields.",
    "social": "Map URLs to linkedin_url, twitter, facebook, instagram. User may skip.",
    "bio": "Save plausible professional bio text only; re-ask gibberish.",
    "professional_memberships": "Extract title, organization, start_date, end_date, is_current objects; user may skip.",
    "preferred_speaking_time": "Save only canonical values: 10-minute, 20-minute, 30-minute, 40-minute, 1 hour.",
    "topics": "Show database topic bullets when asking; save exact names only; off-list → profile update later message.",
    "speaking_formats": "Show database format bullets when asking; exact names only; off-list → add later in profile.",
    "delivery_mode": "Show database delivery bullets when asking; exact names only; off-list → add later in profile.",
    "target_audiences": "Show database audience bullets when asking; exact names only; off-list → add later in profile.",
    "talk_description": "Save as object with title and overview from user text.",
    "key_takeaways": "Array of strings; user may skip.",
    "past_speaking_examples": "Array of {organization_name, event_name, date_month_year}; user may skip.",
    "video_links": "YouTube URLs; user may skip.",
    "testimonial": "Array of quote strings; user may skip.",
}

STEP_QUESTIONS: Dict[str, str] = {
    CREATE_STEP: (
        "Collect professional name, title, and company; then email and phone; then create profile."
    ),
    "location": _QUESTION_LOCATION,
    "social": _QUESTION_SOCIAL,
    "bio": _QUESTION_BIO,
    "professional_memberships": _QUESTION_MEMBERSHIPS,
    "preferred_speaking_time": _QUESTION_SPEAKING_TIME,
    "topics": _QUESTION_TOPICS,
    "speaking_formats": _QUESTION_FORMATS,
    "delivery_mode": _QUESTION_DELIVERY,
    "target_audiences": _QUESTION_AUDIENCES,
    "talk_description": _QUESTION_TALK,
    "key_takeaways": _QUESTION_TAKEAWAYS,
    "past_speaking_examples": _QUESTION_PAST_SPEAKING,
    "video_links": _QUESTION_VIDEO,
    "testimonial": _QUESTION_TESTIMONIAL,
}

_PST_NORMALIZE_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"^1\s*hr\.?$|^1\s*hour$", re.I), "1 hour"),
    (re.compile(r"^10\s*min", re.I), "10-minute"),
    (re.compile(r"^20\s*min", re.I), "20-minute"),
    (re.compile(r"^30\s*min", re.I), "30-minute"),
    (re.compile(r"^40\s*min", re.I), "40-minute"),
]

_SKIP_RE = re.compile(
    r"\b(skip|no thanks|not now|none|nothing|pass|later|don'?t have|no video|no testimonial)\b",
    re.I,
)
_CONTINUE_RE = re.compile(
    r"\b(yes|yeah|yep|sure|ok|okay|continue|go ahead|let'?s go|proceed)\b",
    re.I,
)


def _nonempty_str(v: Any) -> bool:
    return bool(str(v or "").strip())


def normalize_preferred_speaking_times(raw: Any, filter_enum_fn) -> List[str]:
    """Map friendly durations (e.g. '30 min') to canonical values before enum filter."""
    if raw is None:
        return []
    items: List[str] = []
    if isinstance(raw, list):
        items = [str(x).strip() for x in raw if str(x).strip()]
    elif isinstance(raw, str) and raw.strip():
        items = [p.strip() for p in re.split(r"[\n,;]+", raw) if p.strip()]
    canonical: List[str] = []
    for item in items:
        mapped = item
        for pat, repl in _PST_NORMALIZE_PATTERNS:
            if pat.search(item.strip()):
                mapped = repl
                break
        canonical.append(mapped)
    return filter_enum_fn(canonical, _PREFERRED_SPEAKING_TIMES)


def detect_skip_intent(message: str) -> bool:
    """True when the user declines an optional/skippable onboarding question."""
    text = (message or "").strip()
    if not text:
        return False
    if _SKIP_RE.search(text):
        return True
    lowered = text.lower().rstrip(".!")
    if lowered in ("no", "nope", "nah", "n/a", "na"):
        return True
    if re.match(
        r"^(no\b|nope|nah|not really|i\s+don'?t|i\s+do\s+not|don'?t\s+have|do\s+not\s+have|haven'?t|have\s+none)\b",
        lowered,
    ):
        return True
    if re.search(
        r"\b(don'?t\s+have\s+any|do\s+not\s+have\s+any|no\s+past\s+speaking|no\s+examples?|i\s+dont\s+have)\b",
        lowered,
    ):
        return True
    return False


def detect_continue_intent(message: str) -> bool:
    return bool(_CONTINUE_RE.search(message or ""))


def detect_multi_field_answer(message: str, expected_step: str) -> bool:
    """Allow multiple fields in one upsert only when the user clearly bundled answers."""
    text = (message or "").strip()
    if not text:
        return False
    if expected_step == CREATE_STEP:
        return "@" in text or len(text) > 40
    if expected_step == "location":
        return "," in text or len(text.split()) >= 3
    return len(text) > 280 or text.count("\n") >= 2


def step_filled_in_profile(profile: Optional[dict], step: str) -> bool:
    if not profile:
        return False
    if step == "location":
        return all(_nonempty_str(profile.get(k)) for k in ("address_city", "address_state", "address_country"))
    if step == "social":
        return any(_nonempty_str(profile.get(k)) for k in ("linkedin_url", "twitter", "facebook", "instagram"))
    if step == "bio":
        return _nonempty_str(profile.get("bio"))
    if step == "professional_memberships":
        pm = profile.get("professional_memberships")
        return isinstance(pm, list) and len(pm) > 0
    if step == "preferred_speaking_time":
        p = profile.get("preferred_speaking_time")
        if isinstance(p, list):
            return len(p) > 0
        return _nonempty_str(p)
    if step == "topics":
        t = profile.get("topics")
        return isinstance(t, list) and len(t) > 0
    if step == "speaking_formats":
        sf = profile.get("speaking_formats")
        return isinstance(sf, list) and len(sf) > 0
    if step == "delivery_mode":
        dm = profile.get("delivery_mode")
        return isinstance(dm, list) and len(dm) > 0
    if step == "target_audiences":
        t = profile.get("target_audiences")
        return isinstance(t, list) and len(t) > 0
    if step == "talk_description":
        td = profile.get("talk_description")
        if isinstance(td, dict):
            return _nonempty_str(td.get("title")) or _nonempty_str(td.get("overview"))
        return _nonempty_str(td)
    if step == "key_takeaways":
        kt = profile.get("key_takeaways")
        if isinstance(kt, list):
            return len(kt) > 0
        return _nonempty_str(kt)
    if step == "past_speaking_examples":
        ps = profile.get("past_speaking_examples")
        return isinstance(ps, list) and len(ps) > 0
    if step == "video_links":
        v = profile.get("video_links")
        return isinstance(v, list) and len(v) > 0
    if step == "testimonial":
        tm = profile.get("testimonial")
        if isinstance(tm, list):
            return len(tm) > 0
        return _nonempty_str(tm)
    return False


def step_satisfied(
    profile: Optional[dict],
    steps_done: List[str],
    step: str,
) -> bool:
    if step in steps_done:
        return True
    if step in SKIPPABLE_STEPS:
        return False
    return step_filled_in_profile(profile, step)


def derive_expected_step(
    profile: Optional[dict],
    steps_done: List[str],
    *,
    has_profile: bool,
) -> str:
    if not has_profile:
        return CREATE_STEP
    for step in POST_CREATE_STEP_ORDER:
        if not step_satisfied(profile, steps_done, step):
            return step
    return "testimonial"


def all_onboarding_steps_done(
    profile: Optional[dict],
    steps_done: List[str],
    *,
    has_profile: bool,
) -> bool:
    if not has_profile:
        return False
    return all(step_satisfied(profile, steps_done, s) for s in POST_CREATE_STEP_ORDER)


def filter_upsert_args(
    args: dict,
    step: str,
    allow_multi: bool,
    *,
    has_profile: bool,
) -> Tuple[dict, Dict[str, Any], List[str]]:
    """
    Keep only fields allowed for the current step (plus speaker_profile_id).
    Returns (filtered_args, stripped_fields, warnings).
    """
    warnings: List[str] = []
    if not step:
        return dict(args), {}, warnings

    allowed = set(STEP_UPSERT_FIELDS.get(step, set()))
    if not has_profile:
        allowed = set(STEP_UPSERT_FIELDS[CREATE_STEP])

    stripped: Dict[str, Any] = {}
    filtered: Dict[str, Any] = {}
    for k, v in (args or {}).items():
        if k == "speaker_profile_id":
            if v:
                filtered[k] = v
            continue
        if k in allowed:
            filtered[k] = v
        elif v is not None and v != "" and v != []:
            stripped[k] = v

    if stripped and not allow_multi:
        warnings.append(
            f"Rejected fields not part of current step '{step}': {', '.join(sorted(stripped))}. "
            f"Save only: {', '.join(sorted(allowed))}."
        )
    return filtered, stripped, warnings


def should_set_catalog_pending(
    upsert_step: str,
    saved_fields: List[str],
    stripped: Dict[str, Any],
    tc_args: dict,
) -> bool:
    """Off-list catalog answer: no field saved — wait for user to confirm continue."""
    if upsert_step not in CATALOG_STEPS:
        return False
    catalog_field = upsert_step
    attempted = catalog_field in (tc_args or {}) or catalog_field in stripped
    if not attempted:
        return False
    return catalog_field not in (saved_fields or [])


def steps_from_saved_fields(saved_fields: List[str]) -> List[str]:
    """Map upsert saved_fields to onboarding step ids."""
    if not saved_fields:
        return []
    saved = set(saved_fields)
    out: List[str] = []
    for step, fields in STEP_UPSERT_FIELDS.items():
        if fields & saved:
            out.append(step)
    return out


def merge_steps_done(steps_done: List[str], new_steps: List[str]) -> List[str]:
    merged = list(steps_done or [])
    for s in new_steps:
        if s and s not in merged:
            merged.append(s)
    return merged


def may_mark_profile_complete(
    profile: Optional[dict],
    steps_done: List[str],
    *,
    has_profile: bool,
    user_turn_answered_last_question: bool,
) -> bool:
    """
    Completion only after the user has replied to the final question (testimonial).
    user_turn_answered_last_question: True when derive_expected_step was testimonial at turn start.
    """
    if not has_profile or not profile:
        return False
    if not user_turn_answered_last_question:
        return False
    return all_onboarding_steps_done(profile, steps_done, has_profile=True)


def build_checkpoint_for_prompt(
    profile: Optional[dict],
    steps_done: List[str],
    *,
    has_profile: bool,
    preferred_speaking_times: Optional[List[str]] = None,
) -> str:
    """
    Server-derived NEXT_SAVE hints (bio, catalog, optionals, etc.) — not read aloud to the user.
    Aligns with derive_expected_step so the model knows what to upsert after each answer.
    """
    if not profile or not has_profile:
        return ""
    step = derive_expected_step(profile, steps_done, has_profile=True)
    fields = ", ".join(sorted(STEP_UPSERT_FIELDS.get(step, set()))) or "see step"
    pst = preferred_speaking_times or _PREFERRED_SPEAKING_TIMES
    hints: Dict[str, str] = {
        "location": (
            "NEXT_SAVE: location — when the user answers, call upsert_speaker_profile with "
            "address_city, address_state, address_country in this same turn (tool_calls), not text only."
        ),
        "social": (
            "NEXT_SAVE: social URLs — if they provide URLs, upsert linkedin_url/twitter/facebook/instagram same turn; "
            "if they skip, do not loop—continue to bio next."
        ),
        "bio": (
            "NEXT_SAVE: bio — when the user sends bio text, you MUST call upsert_speaker_profile with bio "
            "in this same assistant turn (tool_calls), not text only."
        ),
        "professional_memberships": (
            "NEXT_SAVE: professional_memberships — upsert array of {title, organization, start_date, end_date, is_current} when they answer; "
            "if they skip, proceed without saving junk."
        ),
        "preferred_speaking_time": (
            "NEXT_SAVE: preferred_speaking_time — upsert as array of strings from allowed values: "
            + ", ".join(pst)
            + " in this same turn."
        ),
        "topics": (
            "NEXT_SAVE: topics — show TOPICS bullets from ALLOWED VALUES when asking; upsert exact catalog names only same turn. "
            "Off-list → tell user they can add or change it anytime from their speaker profile; do not save off-list text."
        ),
        "speaking_formats": (
            "NEXT_SAVE: speaking_formats — show SPEAKING FORMATS bullets when asking; upsert catalog matches only same turn. "
            "Off-list → they can add or change it anytime from their speaker profile."
        ),
        "delivery_mode": (
            "NEXT_SAVE: delivery_mode — show DELIVERY MODE bullets when asking; upsert catalog matches only same turn. "
            "Off-list → they can add or change it anytime from their speaker profile."
        ),
        "target_audiences": (
            "NEXT_SAVE: target_audiences — show TARGET AUDIENCES bullets when asking; upsert catalog matches only same turn. "
            "Off-list → they can add or change it anytime from their speaker profile."
        ),
        "talk_description": (
            "NEXT_SAVE: talk_description — upsert as object {title, overview} in the same turn as their answer."
        ),
        "key_takeaways": (
            "NEXT_SAVE: key_takeaways — upsert array of strings in the same turn; re-ask gibberish, do not save junk."
        ),
        "past_speaking_examples": (
            "NEXT_SAVE: past_speaking_examples — upsert array of {organization_name, event_name, date_month_year} same turn."
        ),
        "video_links": (
            "NEXT_SAVE: video_links — upsert YouTube URLs same turn, or skip and move on."
        ),
        "testimonial": (
            "NEXT_SAVE: testimonial — upsert quotes same turn if provided; after their reply call mark_profile_complete."
        ),
    }
    line = hints.get(step) or STEP_GUIDELINES.get(step, "")
    return (
        "INTERNAL_ONBOARDING_CHECKPOINT (for you only; do not read aloud): "
        f"Current step={step}. Fields to save: {fields}. {line}"
    )


def build_all_questions_block(catalog: Optional[Dict[str, List[str]]] = None) -> str:
    """Ordered step list for the system prompt (no catalog bullets here—those are built per turn)."""
    lines = [
        "ONBOARDING SCRIPT (strict order; exactly ONE question per assistant message):",
        "Before profile exists: collect professional name, title, and company; then email and phone; then one upsert.",
    ]
    for i, step in enumerate(POST_CREATE_STEP_ORDER, 1):
        q = STEP_QUESTIONS.get(step, "")
        if step in CATALOG_STEPS:
            lines.append(f"{i}. [{step}] {q} (options list is supplied separately for the active step only).")
        else:
            lines.append(f"{i}. [{step}] {q}")
    lines.append(
        f"{len(POST_CREATE_STEP_ORDER) + 1}. After the user replies to testimonial (last question): "
        "call mark_profile_complete, then send ONLY the completion message."
    )
    return "\n".join(lines)


def build_catalog_allowed_values_block(catalog: Optional[Dict[str, List[str]]]) -> str:
    """Full system-catalog snapshot for topics, formats, delivery, audiences (shown when asking)."""
    if not catalog:
        return ""
    sections: List[str] = []
    titles = {
        "topics": "TOPICS (user may choose multiple — exact names only):",
        "speaking_formats": "SPEAKING FORMATS (choose one or more — exact names only):",
        "delivery_mode": "DELIVERY MODE (choose one or more — exact names only):",
        "target_audiences": "TARGET AUDIENCES (user may choose multiple — exact names only):",
    }
    for key in ("topics", "speaking_formats", "delivery_mode", "target_audiences"):
        names = catalog.get(key) or []
        body = "\n".join(f"• {n}" for n in names) if names else "• (no options loaded)"
        sections.append(f"{titles[key]}\n{body}")
    return (
        "ALLOWED VALUES FROM DATABASE (system catalog only—use EXACT names below when asking and in upsert):\n\n"
        + "\n\n".join(sections)
    )


def _catalog_choice_bullets(step: str, catalog: Optional[Dict[str, List[str]]]) -> str:
    """Bullet list for one catalog step — DB names only, one option per line (<br> for chat UI)."""
    if step not in CATALOG_STEPS or not catalog:
        return ""
    names = [str(n).strip() for n in (catalog.get(step) or []) if str(n).strip()]
    if not names:
        return ""
    # Single \\n collapses in many chat UIs; <br> forces one option per visible line
    bullets = "<br>".join(f"• {n}" for n in names)
    titles = {
        "topics": "You can choose one or more from the list below:",
        "speaking_formats": "You can choose one or more from the list below:",
        "delivery_mode": "You can choose one or more from the list below:",
        "target_audiences": "You can choose one or more from the list below:",
    }
    return f"<br><br>{titles.get(step, 'Options')}<br><br>{bullets}"


def build_step_user_message(step: str, catalog: Optional[Dict[str, List[str]]] = None) -> str:
    """
    Full user-facing message for the current step, including catalog bullets when applicable.
    Preferred speaking time already embeds bullets in STEP_QUESTIONS.
    """
    if step == "preferred_speaking_time":
        return _QUESTION_SPEAKING_TIME
    if step in CATALOG_STEPS:
        base = STEP_QUESTIONS.get(step, "")
        extra = _catalog_choice_bullets(step, catalog)
        if extra:
            return f"{base}{extra}"
        return (
            f"{base}\n\n"
            "(Topic options are not available right now—call get_allowed_values for this step.)"
        )
    return STEP_QUESTIONS.get(step, "")


_LIST_INTRO_PHRASE = "you can choose one or more from the list below"


def _catalog_step_being_asked(content: str) -> Optional[str]:
    """Detect which catalog question the assistant is asking (by question text in the reply)."""
    if not content:
        return None
    for step in POST_CREATE_STEP_ORDER:
        if step not in CATALOG_STEPS:
            continue
        q = STEP_QUESTIONS.get(step, "")
        if q and q in content:
            return step
    return None


def _extract_ack_before_catalog_question(content: str, step: str) -> str:
    """Keep only the short ack lines before the catalog question (drop echoed option lists)."""
    q = STEP_QUESTIONS.get(step, "")
    if q and q in content:
        before = content.split(q, 1)[0].strip()
        if before:
            return before.replace("\n", "<br>").strip()
    lines: List[str] = []
    for line in re.split(r"<br>|\n", content):
        s = line.strip()
        if not s:
            if lines:
                break
            continue
        if _LIST_INTRO_PHRASE in s.lower():
            break
        if s.startswith("•"):
            break
        if STEP_QUESTIONS.get(step, "") and STEP_QUESTIONS.get(step, "") in s:
            break
        lines.append(s)
    return "<br>".join(lines).strip()


def finalize_catalog_question_reply(
    step: str,
    content: str,
    catalog: Optional[Dict[str, List[str]]],
) -> str:
    """
    Server-built catalog question only: ack from LLM + DB bullet list (one per line).
    Ignores any inline '• A • B • C' text the model invented.
    """
    if step not in CATALOG_STEPS or not catalog:
        return content
    names = catalog.get(step) or []
    if not names:
        return content
    body = build_step_user_message(step, catalog)
    ack = _extract_ack_before_catalog_question(content, step)
    return f"{ack}<br><br>{body}" if ack else body


_CATALOG_CHOICE_RULES = (
    "CATALOG STEPS (topics, speaking_formats, delivery_mode, target_audiences): "
    "Allowed values come from the database system catalog ONLY—use ONLY names in the user message template. "
    "FORBIDDEN: inventing, suggesting, or adding options not in that list. "
    "Options are formatted one per line in the template (do not squeeze into one line with ' • ' between items). "
    "Do NOT duplicate the list or paste all catalog categories in one message. "
    "When the user answers a catalog step: FIRST call upsert_speaker_profile (tool_calls) with only exact catalog matches. "
    "Do NOT say a field was saved unless the tool result saved_fields includes that field. "
    "Do NOT ask the next step's question in the same assistant message as the upsert tool call—after the tool returns, "
    "your following assistant message may ack and ask the next question. "
    "If the user names something NOT on the list: omit that field in upsert; in your text reply tell them they can add or "
    "change it anytime from their speaker profile (use first name when known). Do NOT claim you saved off-list wording. "
    "Then ask the next step with that step's bullet list."
)


_FRIENDLY_ASSISTANT_TONE = (
    "You are a friendly, warm onboarding assistant for Human Driven AI (SpeakerPitcher). "
    "Tone: conversational, professional, and supportive—like a helpful colleague guiding them through their profile. "
    "Use second person (you/your). Never sound robotic, demanding, or bureaucratic."
)

_CONVERSATIONAL_ACK_RULE = (
    "CONVERSATIONAL WRAPPER (required on every turn except completion): In the SAME assistant message, "
    "start with ONE short friendly acknowledgment of their last answer (≤25 words; use their professional full_name from the profile when known, "
    "e.g. 'Thanks, Jane Doe!' or 'Great, Alex Chen!'). "
    "Then ask the next question verbatim from the script (blank line between ack and question is fine). "
    "Never ask the next question without a brief ack first. "
    "For catalog steps: ack → short intro line for that field → bullet list → then wait for their answer."
)

_STRICT_ONBOARDING_SCOPE_RULE = (
    "If the user asks an unrelated, off-topic question, reply exactly: "
    "\"I can only help with your SpeakerPitcher profile onboarding right now.\" "
    "Then ask the current onboarding question verbatim."
)


def build_onboarding_script_prompt(
    expected_step: str,
    *,
    has_profile: bool,
    speaker_profile_id: Optional[str] = None,
    catalog: Optional[Dict[str, List[str]]] = None,
) -> str:
    """Current-step focus plus full question script."""
    order = " → ".join(([CREATE_STEP] if not has_profile else []) + POST_CREATE_STEP_ORDER)
    user_message = build_step_user_message(expected_step, catalog)
    fields_now = ", ".join(sorted(STEP_UPSERT_FIELDS.get(expected_step, set())))
    parts = [
        build_all_questions_block(catalog),
        "",
        "RULES:",
        _CONVERSATIONAL_ACK_RULE,
        "- Ask exactly ONE question per assistant message.",
        "- After the user answers each question, you MUST call upsert_speaker_profile (tool_calls) before your reply text. "
        f"Pass only fields for that step ({fields_now} when on {expected_step}). "
        "Text-only replies do NOT save. Never say 'saved' or 'recorded' unless saved_fields in the tool result includes that field.",
        "- Do NOT call mark_profile_complete until the user has replied to the testimonial question (last step).",
        f"STEP ORDER: {order}.",
        f"NOW (ask this step only): {expected_step}.",
        "YOUR MESSAGE TO THE USER (after optional short ack, include ALL of this—especially every bullet line):",
        user_message,
    ]
    if expected_step in CATALOG_STEPS:
        parts.append(_CATALOG_CHOICE_RULES)
        parts.append(
            "Do NOT paste or invent extra options—the user message template above is the complete database list for this step. "
            "Never repeat 'You can choose one or more from the list below' twice."
        )
    if speaker_profile_id:
        parts.append(f'Always pass speaker_profile_id="{speaker_profile_id}" on upsert and mark_profile_complete.')
    if not has_profile and expected_step == CREATE_STEP:
        parts.append(
            "No profile yet: collect name, title, company, then email+phone, then one upsert with all five fields (omit speaker_profile_id)."
        )
    if expected_step in SKIPPABLE_STEPS:
        parts.append("User may skip; call upsert only if they provided data, then ask the next step.")
    if expected_step == "testimonial":
        parts.append(
            "This is the LAST question. After the user's reply (answer or skip), call upsert if they gave content, "
            "then mark_profile_complete, then ONLY the completion message."
        )
    return "\n".join(parts)


def build_simple_system_prompt(
    *,
    has_profile: bool,
    profile_json: str,
    speaker_profile_id: Optional[str],
    expected_step: str,
    catalog: Optional[Dict[str, List[str]]] = None,
    checkpoint_line: Optional[str] = None,
) -> str:
    """Step-based system prompt with checkpoints, friendly tone, and ack-before-question."""
    script = build_onboarding_script_prompt(
        expected_step,
        has_profile=has_profile,
        speaker_profile_id=speaker_profile_id,
        catalog=catalog,
    )
    checkpoint_block = ""
    if checkpoint_line and checkpoint_line.strip():
        checkpoint_block = checkpoint_line.strip() + "\n\n"
    if not has_profile:
        return (
            _FRIENDLY_ASSISTANT_TONE
            + "\n\n"
            + _STRICT_ONBOARDING_SCOPE_RULE
            + "\n\n"
            "Before profile exists (one question at a time):\n"
            "1) First ask for professional name, title, and company (warm, friendly welcome).\n"
            "2) Acknowledge warmly, then say exactly: Thanks for joining SpeakerPitcher! Let's build your profile so we can find the right opportunities for you. Then ask email and phone in the same message.\n"
            "3) When you have all five fields, call upsert_speaker_profile once (no speaker_profile_id).\n"
            "Do NOT call upsert until step 3. After create, acknowledge and ask location (first post-create question) in one message.\n\n"
            + _CONVERSATIONAL_ACK_RULE
            + "\n\nNever mention user login, passwords, or credentials email.\n\n"
            + checkpoint_block
            + script
        )
    return (
        _FRIENDLY_ASSISTANT_TONE
        + "\n\n"
        + _STRICT_ONBOARDING_SCOPE_RULE
        + "\n"
        f"Profile in database: {profile_json}\n"
        + checkpoint_block
        + script
    )
