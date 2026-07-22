"""
Speaker Profile Chatbot Service: LLM-driven create/update via tool calls.
Flow: user message -> LLM -> tool call -> create/update profile -> ChatSession -> return.
"""
import json
import logging
import os
import re
import secrets
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from openai import OpenAI
from pydantic import EmailStr, TypeAdapter, ValidationError

from app.email.welcome_account import try_send_welcome_email_on_account_created
from app.helpers.auth_roles import is_admin_role
from app.schemas.User import default_user_subscription
from app.helpers.SpeakerCredentialsEmail import send_speaker_credentials_email
from app.helpers.Utilities import Utils
from app.schemas.User import UserType

from app.config.speaker_profile_chatbot import (
    MANDATORY_FIELDS,
    MANDATORY_FIELDS_DISPLAY,
    OPTIONAL_FIELDS,
    OPTIONAL_FIELDS_DISPLAY,
)
from app.models.SpeakerProfile import PROFILE_FIELDS
from app.services.speaker_profile_chatbot_steps import (
    CATALOG_STEPS,
    CREATE_STEP,
    SKIPPABLE_STEPS,
    build_checkpoint_for_prompt,
    build_identity_welcome_reply,
    build_simple_system_prompt,
    derive_expected_step,
    derive_pre_create_subphase,
    extract_pre_create_identity,
    ensure_catalog_list_in_reply,
    PRE_CREATE_POST_WELCOME,
    PRE_CREATE_PROMPT_WELCOME,
    PRE_CREATE_READY,
    _SPEAKERPITCHER_WELCOME_LINE,
    speakerpitcher_welcome_already_sent,
    strip_duplicate_speakerpitcher_welcome,
    detect_skip_intent,
    may_mark_profile_complete,
    merge_steps_done,
    steps_from_saved_fields,
)

logger = logging.getLogger(__name__)


def _jwt_user_id(user: Optional[Dict[str, Any]]) -> Optional[str]:
    if not user:
        return None
    raw = user.get("id") or user.get("user_id") or user.get("_id")
    return str(raw) if raw is not None else None


def _first_name_from_full_name(full_name: str) -> str:
    """First given name for conversational acks (strip credentials after comma)."""
    raw = (full_name or "").strip()
    if not raw:
        return ""
    before_credential = raw.split(",")[0].strip()
    parts = before_credential.split()
    return parts[0] if parts else ""


def _merge_pending_identity_into_args(
    args: Dict[str, Any],
    pending: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    if not pending:
        return args
    merged = dict(args)
    for field in ("full_name", "professional_title", "company"):
        if not (merged.get(field) or "").strip():
            val = (pending.get(field) or "").strip()
            if val:
                merged[field] = val
    return merged


def _full_name_for_user_account(email: str, full_name: str) -> str:
    """Ensure 2–50 chars for create_speaker_user; chatbot may only have email local-part early."""
    fn = (full_name or "").strip()
    if len(fn) > 50:
        return fn[:50]
    if len(fn) >= 2:
        return fn
    local = (email.split("@")[0] if email else "").strip() or "speaker"
    base = fn if fn else local
    if len(base) < 2:
        base = "Speaker"
    return base[:50]


_EMAIL_REGEX = re.compile(
    r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*\.[a-zA-Z]{2,}"
)
_PHONE_LIKE_REGEX = re.compile(r"(?:\+?\d[\d\s().-]{6,}\d)")


def _extract_emails_from_text(text: str) -> List[str]:
    """Unique emails in message order (lowercased)."""
    found = _EMAIL_REGEX.findall(text or "")
    out: List[str] = []
    seen = set()
    for raw in found:
        email = (raw or "").strip().lower()
        if email and email not in seen:
            seen.add(email)
            out.append(email)
    return out


def _looks_like_phone_number(text: str) -> bool:
    """True when the message looks like a phone number rather than email/other chatter."""
    t = (text or "").strip()
    if not t or "@" in t:
        return False
    digits = sum(1 for c in t if c.isdigit())
    return digits >= 7 and bool(_PHONE_LIKE_REGEX.search(t))


def _is_valid_email(value: str) -> bool:
    email = (value or "").strip().lower()
    return bool(email and _EMAIL_REGEX.fullmatch(email))

# Steps for profile completion (excl. pre-create identity + email/phone). Catalog required, then remaining optionals.
_CHATBOT_REQUIRED_STEPS = ["topics", "speaking_formats", "delivery_mode", "target_audiences"]
_CHATBOT_OPTIONAL_STEPS = [
    "talk_description",
    "key_takeaways",
    "past_speaking_examples",
    "video_links",
    "testimonial",
]

# Fixed multiselect for preferred speaking duration (chat flow).
_PREFERRED_SPEAKING_TIMES = ["10-minute", "20-minute", "30-minute", "40-minute", "1 hour"]

_CHAT_LOCATION_QUESTION = (
    "What city, state or province, and country are you based in? "
    "You can answer in one line (e.g. Austin, Texas, United States)."
)
_CHAT_SOCIAL_QUESTION = (
    "Share your primary, professional social media channel URLs "
    "(e.g., LinkedIn, Facebook, X, Instagram, etc.)."
)
_CHAT_BIO_QUESTION = (
    "Please share your professional bio in 50 - 100 words."
)
_CHAT_SPEAKING_TIME_QUESTION = (
    "What is your preferred speaking time?\n\n"
    "Choose one or more from the list below:\n\n"
    "• 10-minute\n"
    "• 20-minute\n"
    "• 30-minute\n"
    "• 40-minute\n"
    "• 1 hour"
)

_PAST_SPEAKING_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "organization_name": {"type": "string", "description": "Host organization or company"},
        "event_name": {"type": "string", "description": "Event or conference name if known; optional"},
        "date_month_year": {"type": "string", "description": "When, e.g. March 2024"},
    },
}

_PROFESSIONAL_MEMBERSHIP_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "Membership or credential title (e.g. Certified Member, Fellow)"},
        "organization": {"type": "string", "description": "Professional body, association, or organization name"},
        "start_date": {"type": "string", "description": "Membership start date (e.g. 2024-01-01)"},
        "end_date": {"type": "string", "description": "Membership end date; keep empty when currently active"},
        "is_current": {"type": "boolean", "description": "Whether they are currently an active member"},
    },
}

# User-facing question only (no field-by-field template); LLM extracts structure for DB.
_PAST_SPEAKING_CHAT_QUESTION = (
   "Do you have past speaking examples you'd like to share? Please include the organization or event name and the corresponding date (month/year)."
)

# After professional bio; optional—user may skip; LLM maps free text to structured rows.
_PROFESSIONAL_MEMBERSHIPS_CHAT_QUESTION = (
    "Please share your Professional Memberships, (e.g. Role, Organization and topics)."
)

_SOCIAL_URL_FIELD_RULES = (
    "When the user pastes profile URLs, you MUST call upsert_speaker_profile in the same turn and map each URL to exactly one field: "
    "linkedin.com → linkedin_url; twitter.com or x.com → twitter; instagram.com → instagram; facebook.com → facebook. "
    "Use the full URL string. Example: https://www.linkedin.com/in/alex-robinson-analytics → linkedin_url; "
    "https://twitter.com/alexrobinson_ai or https://x.com/... → twitter; https://www.instagram.com/... → instagram; "
    "https://www.facebook.com/... → facebook. Pass only the fields they provided; omit others."
)

_INVALID_FIXED_LIST_GUIDANCE = (
    "Tell the user in one short sentence (second person—use 'you/your', not 'they/their'). "
    "When full_name is known, start with their first name—e.g. "
    "'Alex, that choice isn't on this list, but you can add it later from your speaker profile.' "
    "If you don't have a name: 'That choice isn't on this list, but you can add it later from your speaker profile.' "
    "FORBIDDEN when nothing matched: do NOT say you saved their free-text wording. "
    "In the SAME assistant message, do NOT ask 'would you like to continue?'; do NOT re-ask the same field—"
    "write only that short add-later line (plus confirming any saved matches if mixed); the server appends the NEXT question."
)

# Off-catalog answers: acknowledge + advance to next question in the same turn.
_FIXED_LIST_ADVANCE_AFTER_OFF_LIST = (
    "OFF-LIST ONLY (topics, speaking_formats, delivery_mode, target_audiences, preferred_speaking_time): "
    "If NOTHING the user named matches an allowed value, treat the step as DONE. "
    "Omit the field in upsert (or pass nothing that saves). "
    "In ONE assistant message: say those choices aren't on the allowed list, but they can add them later from their speaker profile. "
    "Then stop—do NOT re-ask this step, do NOT ask continue?, do NOT paste the next question/bullets (the server appends the next step)."
)

# Mixed allowed + not allowed: save matches, mention others, advance same turn.
_FIXED_LIST_PARTIAL_OR_MIXED_FLOW = (
    "MIXED CATALOG / FIXED-LIST ANSWERS (topics, speaking_formats, delivery_mode, target_audiences, preferred_speaking_time): "
    "When some named items match allowed values and some do not: upsert ONLY the matched values. "
    "In ONE assistant message: (1) briefly confirm the exact allowed name(s) you saved; "
    "(2) say the other named item(s) aren't on the list but they can add them later from their speaker profile. "
    "Then stop—do NOT ask continue?, do NOT paste the next question/bullets (server appends next step). "
    "FULL MATCH (everything allowed): brief ack only; server appends the next step—no 'not on the list' language."
)

# Prevent "I've saved your selection as 'train'" when train was never written to the profile.
_FIXED_LIST_USER_FACING_TRUTH = (
    "TRUTHFUL COPY FOR CATALOG FIELDS (topics, speaking_formats, delivery_mode, target_audiences): "
    "What you say to the user MUST match upsert_speaker_profile in the same turn. "
    "NEVER claim you saved, recorded, added, or stored a value for one of these fields using the user's free text (e.g. quoting 'train') "
    "unless that exact string is an allowed catalog name you included in the tool arguments for that field. "
    "If you omitted the field or passed an empty list because nothing matched, say only that their choice isn't on the list and they can update from their profile—do not describe their invalid wording as saved. "
    "For partial/mixed answers, only name persisted catalog matches; for the rest, say not on the list / add from profile—never both 'saved as X' and 'not on the list' for the same X. "
    "Never claim the user mentioned 'other options' that are not on the list unless their message clearly included at least one distinct phrase that is not an allowed catalog name for that step."
)

# LLMs often reuse catalog/off-list wording for optional free-text steps—explicitly forbid that.
_FREE_TEXT_NON_CATALOG_RULES = (
    "FREE-TEXT FIELDS — NOT A CATALOG (no pick-list, no 'allowed options'): "
    "bio, talk_description, key_takeaways, professional social URLs (linkedin_url step), professional_memberships, "
    "past_speaking_examples, video_links, testimonial. "
    "FORBIDDEN for these fields: saying the user's words \"aren't on the list\", \"not on the list\", \"off the list\", "
    "\"pick from the list\", \"allowed options\", or any phrasing that implies a predefined menu of choices. "
    "FORBIDDEN: using the OFF-LIST or MIXED catalog 'add later from profile' wording for these free-text fields. "
    "For bio: if the reply is not a plausible professional speaker bio (e.g. single random word, gibberish, joke, unrelated one-liner), "
    "do NOT call upsert_speaker_profile with bio; re-ask for a short professional bio suitable for an event program—no 'list' language. "
    "For key_takeaways: if the reply is not genuine takeaway content (e.g. random word 'peanuts', gibberish, unrelated one-liner), "
    "do NOT call upsert_speaker_profile with key_takeaways; do not save it; give one short friendly reply that it doesn't sound like "
    "real takeaways from their talks and re-ask the same key_takeaways question (or offer to skip)—no 'list' language. "
    "Same idea for talk_description, testimonial, and professional_memberships when the answer is clearly not on-topic for that question. "
    "For professional_memberships: extract title, organization, start_date, end_date, and is_current per membership from natural language; "
    "do not ask users to fill a rigid form or label each key aloud—same spirit as past_speaking_examples."
)

# Backend may create a users row + credentials email when email is first saved; chat must never disclose that.
_CHATBOT_SILENT_PLATFORM_ACCOUNT = (
    "PLATFORM LOGIN (silent, backend-only): When the user's email is first saved, the system may create a platform user and send login details by email. "
    "You MUST NEVER tell the user you created an account, login, password, sign-in, credentials, or that they will get/were sent login information—only discuss their speaker profile onboarding."
)

# Models often compress catalog questions into "A, B, or C?" after seeing compact examples—reinforce bullets.
_CATALOG_OPTIONS_BULLET_FORMAT = (
    "CATALOG CHOICE QUESTIONS (topics, speaking_formats, delivery_mode, target_audiences): Whenever you ask the user to pick from the catalog, "
    "list the choices as bullet points—one option per line using • or - and the EXACT catalog names from get_allowed_values or the snapshot. "
    "Do NOT squeeze options into one sentence with commas or em-dashes (e.g. avoid 'Hybrid, In-person, or Virtual'). "
    "You may add one short intro line before the bullets (e.g. 'What speaking formats do you offer?'). "
    "After an off-list or mixed answer, the NEXT field's options must still be bullets when the server shows that next step. "
    "This is not 'dumping the whole catalog': showing one category's list as bullets is required; forbidden is re-pasting every category when only one step is active."
)

_FIXED_LIST_USER_DEFERS = (
    "If the user says they will skip, or add or change these selections later from their profile "
    "(or similar), do not insist or repeat the full list: briefly tell them they can update their profile anytime "
    "(use first name from full_name when known, e.g. 'Jordan, you can update that anytime from your profile'), then move to the next question."
)

_PROFILE_COMPLETION_MESSAGE = (
    "Your speaker profile has been successfully completed. "
    "You may now close this window and review your profile at your convenience.\n\n"
    "Upon closing this window, you will receive an email containing your login credentials "
    "to access and review your profile online.\n\n"
    "Thank you."
)

# Models often announce "optional fields" to the user; keep onboarding seamless.
_FORBIDDEN_OPTIONAL_FIELDS_TRANSITION_USER_TEXT = (
    "USER-FACING FORBIDDEN: Never announce that you are moving to or starting optional content. "
    "Do NOT say (or close variants of): "
    "Now let's move on to the optional fields; let us move on to the optional fields; "
    "moving on to optional fields; next we'll cover optional fields; the optional fields section; "
    "time for optional fields; optional questions next; we'll ask some optional questions; "
    "the optional part of your profile; or any transition that names optional fields, optional questions, or optional sections. "
    "Jump straight to the next concrete question with CONVERSATIONAL WRAPPER—never label a step as required, optional, or mandatory to the user."
)

# Warmer than bare Q&A; prescribed question strings stay verbatim after the opener.
_CONVERSATIONAL_ACK_BEFORE_QUESTION = (
    "CONVERSATIONAL WRAPPER: Whenever you move to the next profile question (required or optional), begin with ONE short sentence—"
    "acknowledge their last answer, react warmly, or add one helpful line on why the next field matters (second person, professional, ≤25 words). "
    "NAME IN ACKNOWLEDGMENTS: use full_name only on the first acknowledgment after they provide their professional name "
    "(e.g. 'Thanks, Jane Doe!'); on every later turn use first name only (first word before any comma). "
    "Vary your opener every turn—rotate phrasing ('Thanks, Jane!', 'Sounds good, Jane!', 'Perfect, Jane!', 'Got it, Jane!', "
    "'Thanks for sharing that, Jane!', etc.). "
    "FORBIDDEN: starting every message with 'Great, {first name}!' or repeating the same opener on back-to-back turns. "
    "Then ask the next question in the same message. Do not alter wording where instructions require EXACT or verbatim text—paste that question exactly after your opener (blank line between is fine). "
    "For catalog steps, opener → then your short intro line for that field → then bullet list (per CATALOG CHOICE QUESTIONS). "
    "EXCEPTION—WRAPPER: for off-list or mixed catalog answers, your text is only the short add-later (and saved-match confirm if mixed) ack—"
    "do not paste the next field's question or bullets yourself; the server appends the next step after your ack. "
    "Still use ONLY the exact completion message after mark_profile_complete—no preamble there."
)

# Onboarding LLM may only offer catalog rows marked system (plus legacy docs without type).
_CATALOG_TYPE_FOR_LLM = "system"

# Models may batch catalog saves with later optionals; reinforce one upsert per user answer per catalog step.
_CATALOG_UPSERT_EACH_USER_TURN = (
    "CATALOG FIELDS — SAVE ON EVERY ANSWER (CRITICAL): For topics, speaking_formats, delivery_mode, and target_audiences, "
    "you MUST call upsert_speaker_profile in the SAME assistant turn as the user's message that answers that step. "
    "Pass speaker_profile_id and ONLY the field(s) for that step (e.g. only topics when they answered the topics question)—"
    "do not wait until talk_description, key_takeaways, or any later question to persist catalog data. "
    "Do not accumulate answers across multiple user turns and then save them in one later upsert. "
    "Exception: if a single user message clearly answers multiple catalog steps at once, you may include all matching fields in one upsert for that message only. "
    "FORBIDDEN: deferring topics/speaking_formats/delivery_mode/target_audiences saves until after optional fields. "
    "profile_json updates only after each successful upsert—if you skip the tool on a catalog answer, the next turn still looks empty and breaks flow."
)


def _prompt_option_lines(values: List[str], line_prefix: str = "                ") -> str:
    return "\n".join(f"{line_prefix}{v}  " for v in values)


def _prompt_topic_bullet_lines(names: List[str], line_prefix: str = "                ") -> str:
    return "\n".join(f"{line_prefix}• {t}  " for t in names)


def _format_catalog_allowed_values_bullets(catalog: Dict[str, List[str]], line_prefix: str = "                ") -> str:
    """Multi-line bullet block for ALLOWED VALUES in the system prompt."""
    def section(title: str, key: str) -> str:
        names = catalog.get(key) or []
        body = _prompt_topic_bullet_lines(names, line_prefix) if names else f"{line_prefix}• (none)"
        return f"{title}\n{body}"

    return "\n\n".join(
        [
            section("TOPICS (User may choose multiple):", "topics"),
            section("SPEAKING FORMATS:", "speaking_formats"),
            section("DELIVERY MODE:", "delivery_mode"),
            section("TARGET AUDIENCES (User may choose multiple):", "target_audiences"),
        ]
    )


def _build_get_allowed_values_tool() -> dict:
    """Tool for LLM to fetch valid options for topics, speaking_formats, delivery_mode, target_audiences."""
    return {
        "type": "function",
        "function": {
            "name": "get_allowed_values",
            "description": (
                "Fetch allowed values from the database system catalog only (type=system)—no invented options. "
                "Call before validating user free text against these fields."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "value_type": {
                        "type": "string",
                        "enum": ["topics", "speaking_formats", "delivery_mode", "target_audiences"],
                        "description": "Which field's allowed values to fetch.",
                    },
                },
                "required": ["value_type"],
            },
        },
    }


def _build_mark_profile_complete_tool(speaker_profile_id: Optional[str] = None) -> dict:
    """Tool for LLM to mark profile complete only after the user replies to testimonial (last question)."""
    desc = (
        "Call this ONLY after the user has replied to the testimonial question (the last step). "
        "Do NOT call when asking testimonial or while any earlier step is still open. "
        "Call in the same turn as the upsert for testimonial (if any), then respond with ONLY the completion message."
    )
    return {
        "type": "function",
        "function": {
            "name": "mark_profile_complete",
            "description": desc,
            "parameters": {
                "type": "object",
                "properties": {
                    "speaker_profile_id": {
                        "type": "string",
                        "description": "Required. The speaker profile id from the chat session.",
                    },
                },
                "required": ["speaker_profile_id"],
            },
        },
    }


def _build_upsert_tool(speaker_profile_id_from_session: Optional[str] = None):
    """Build tool def. When speaker_profile_id_from_session is set, emphasize UPDATE with that id."""
    if speaker_profile_id_from_session:
        desc = (
            f"Update speaker profile. speaker_profile_id is REQUIRED: use \"{speaker_profile_id_from_session}\". "
            "You MUST call this after EVERY user answer that contains profile data for the current onboarding question. "
            "Do NOT reply with text only—include this tool call with the fields to persist in the same turn. "
            "Pass speaker_profile_id and only the fields for the step they just answered. "
            "CONTACT LOCK: never pass email or phone_number on update—those are fixed after create and must not be replaced "
            "even if the user types another email/phone later."
        )
    else:
        desc = (
            "Create new speaker profile. Call once when you have full_name, exactly ONE valid email, and a phone_number "
            "(omit speaker_profile_id). Also pass professional_title and company when the user already provided them. "
            "EMAIL RULES: If the user gives multiple emails, do NOT call this tool—ask which single email to use. "
            "If their reply is not a real email (random text, jokes, unrelated content), do NOT call this tool—"
            "politely say it is not related to email and ask for a proper email address. "
            "After one email is chosen, if phone is still missing, ask for phone before creating. "
            "Until then, collect in chat only. After name+title+company, say once (never repeat on later turns): "
            + _SPEAKERPITCHER_WELCOME_LINE
            + " "
            "then ask for email and phone. Omit speaker_profile_id for create."
        )
    upsert_desc = (
        desc
        + " "
        + _SOCIAL_URL_FIELD_RULES
        + " Map past_speaking_examples and professional_memberships from natural language. "
        + "For topics, speaking_formats, delivery_mode, target_audiences: only save exact names from the database system catalog "
        + "(via get_allowed_values / step template); do not add extra values. "
        + "If ZERO catalog names match: omit that field; tell the user those choices aren't on the list but they can add them later "
        + "from their speaker profile, then advance (server shows the next question)—do not re-ask the same list. "
        + "If MIXED (some match, some don't): save only matches; confirm saved names; say the others aren't on the list but can be "
        + "added later from their speaker profile; then advance."
    )
    return {
        "type": "function",
        "function": {
            "name": "upsert_speaker_profile",
            "description": upsert_desc,
            "parameters": {
                "type": "object",
                "properties": {
                    "speaker_profile_id": {
                        "type": "string",
                        "description": "For UPDATE: REQUIRED, use value from chat session. For CREATE: omit.",
                    },
                    "email": {
                        "type": "string",
                        "description": (
                            "Single valid email for CREATE only. Never pass on UPDATE. "
                            "Never invent or change email after the profile exists."
                        ),
                    },
                    "full_name": {"type": "string", "description": "Professional full name as the speaker wants it displayed"},
                    "professional_title": {"type": "string", "description": "Current job title or role"},
                    "company": {"type": "string", "description": "Company or organization name"},
                    "phone_number": {
                        "type": "string",
                        "description": (
                            "Phone number for CREATE (required before create). Never pass on UPDATE once already saved."
                        ),
                    },
                    "topics": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Exact catalog names from get_allowed_values(topics) / step template only. "
                            "Same turn as user's answer. Zero matches → omit topics; say not allowed but can add later from profile and advance. "
                            "Mixed → pass only matches; mention unmatched can add later from profile and advance."
                        ),
                    },
                    "speaking_formats": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Exact catalog names only. Same turn as user's answer. "
                            "Zero/mixed unmatched → omit non-matches; say can add later from profile; advance (no re-ask)."
                        ),
                    },
                    "delivery_mode": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Exact catalog names only. Same turn as user's answer. "
                            "Zero/mixed unmatched → omit non-matches; say can add later from profile; advance (no re-ask)."
                        ),
                    },
                    "talk_description": {
                        "oneOf": [
                            {"type": "string"},
                            {
                                "type": "object",
                                "properties": {
                                    "title": {"type": "string"},
                                    "overview": {"type": "string"},
                                },
                                "description": "Preferred: short title plus overview after user describes their talk.",
                            },
                        ],
                        "description": "After user describes their talk, save as object with title and overview when possible.",
                    },
                    "target_audiences": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Exact catalog names only. Same turn as user's answer. "
                            "Zero/mixed unmatched → omit non-matches; say can add later from profile; advance (no re-ask). "
                            "Never claim you saved a name you did not pass here."
                        ),
                    },
                    "linkedin_url": {"type": "string", "description": "Full LinkedIn profile URL only (linkedin.com)."},
                    "past_speaking_examples": {
                        "type": "array",
                        "items": _PAST_SPEAKING_ITEM_SCHEMA,
                        "description": (
                            "INTERNAL only: after free-form past engagements, extract one object per engagement: "
                            "organization_name, optional event_name, date_month_year. Do not read keys aloud."
                        ),
                    },
                    "video_links": {"type": "array", "items": {"type": "string"}},
                    "key_takeaways": {
                        "oneOf": [
                            {"type": "string"},
                            {"type": "array", "items": {"type": "string"}},
                        ],
                        "description": "Distinct key takeaways as an array of strings (or one string); save after user answers the key-takeaways question.",
                    },
                    "name_salutation": {"type": "string"},
                    "bio": {"type": "string"},
                    "twitter": {"type": "string", "description": "Full X/Twitter profile URL (twitter.com or x.com)."},
                    "facebook": {"type": "string", "description": "Full Facebook profile URL (facebook.com)."},
                    "instagram": {"type": "string", "description": "Full Instagram profile URL (instagram.com)."},
                    "address_city": {"type": "string"},
                    "address_state": {"type": "string"},
                    "address_country": {"type": "string"},
                    "professional_memberships": {
                        "type": "array",
                        "items": _PROFESSIONAL_MEMBERSHIP_ITEM_SCHEMA,
                        "description": (
                            "Optional. One object per membership: title, organization, start_date, end_date, is_current. "
                            "INTERNAL only: extract from free-text user replies; do not read JSON keys aloud."
                        ),
                    },
                    "preferred_speaking_time": {
                        "oneOf": [
                            {"type": "string"},
                            {"type": "array", "items": {"type": "string"}},
                        ],
                        "description": (
                            "One or more of exactly: 10-minute, 20-minute, 30-minute, 40-minute, 1 hour. "
                            "Use array when user picks multiple. Zero matches → omit; say not allowed but can add later from profile; advance. "
                            "Mixed → save matches only; mention others can add later from profile; advance."
                        ),
                    },
                    "testimonial": {
                        "oneOf": [
                            {"type": "string"},
                            {"type": "array", "items": {"type": "string"}},
                        ],
                        "description": "One or more testimonials as strings (quotes/feedback from past speaking).",
                    },
                },
            },
        },
    }


def _filter_enum_values(values: List[str], allowed: List[str]) -> List[str]:
    if not values:
        return []
    allowed_lower = {a.strip().lower(): a for a in allowed}
    out = []
    seen = set()
    for v in values:
        v = (v or "").strip()
        if not v:
            continue
        key = v.lower()
        if key in allowed_lower and key not in seen:
            seen.add(key)
            out.append(allowed_lower[key])
    return out


def _normalize_preferred_speaking_times(raw: Any) -> List[str]:
    """Coerce tool output to canonical multiselect values for preferred_speaking_time."""
    if raw is None:
        return []
    items: List[str] = []
    if isinstance(raw, list):
        items = [str(x).strip() for x in raw if str(x).strip()]
    elif isinstance(raw, str) and raw.strip():
        items = [p.strip() for p in re.split(r"[\n,;]+", raw) if p.strip()]
    return _filter_enum_values(items, _PREFERRED_SPEAKING_TIMES)


def _nonempty_str(v: Any) -> bool:
    return bool(str(v or "").strip())


def _profile_has_preferred_speaking_time(profile: dict) -> bool:
    p = profile.get("preferred_speaking_time")
    if p is None:
        return False
    if isinstance(p, list):
        return len(p) > 0
    return _nonempty_str(p)


def _profile_has_topics(profile: dict) -> bool:
    t = profile.get("topics")
    return isinstance(t, list) and len(t) > 0


def _onboarding_checkpoint_for_prompt(profile: dict) -> str:
    """
    Server-derived hint so the model knows what to persist next (reduces text-only replies after bio).
    Not shown verbatim to the user.
    """
    if not profile:
        return ""

    def loc_ok() -> bool:
        return all(_nonempty_str(profile.get(k)) for k in ("address_city", "address_state", "address_country"))

    def social_ok() -> bool:
        return any(_nonempty_str(profile.get(k)) for k in ("linkedin_url", "twitter", "facebook", "instagram"))

    parts: List[str] = []
    if not loc_ok():
        parts.append(
            "NEXT_SAVE: location — when the user answers, call upsert_speaker_profile with address_city, address_state, address_country."
        )
    elif not social_ok() and not _nonempty_str(profile.get("bio")):
        parts.append(
            "NEXT_SAVE: social URLs (step B) — ask once; if the user provides URLs, upsert same turn; if they skip with none, continue to bio (step C)—not loop on B."
        )
    elif not _nonempty_str(profile.get("bio")):
        parts.append(
            "NEXT_SAVE: bio (step C) — when the user sends bio text, you MUST call upsert_speaker_profile with bio in this same assistant turn (tool_calls), not text only. "
            "Next in order: optional professional memberships (step D) after bio, then preferred speaking time (step E)."
        )
    elif not _profile_has_preferred_speaking_time(profile):
        pm = profile.get("professional_memberships")
        memberships_saved = isinstance(pm, list) and len(pm) > 0
        if memberships_saved:
            parts.append(
                "NEXT_SAVE: preferred_speaking_time (step E) — when the user picks durations, you MUST call upsert_speaker_profile "
                f"with preferred_speaking_time as an array from {_PREFERRED_SPEAKING_TIMES} in this same assistant turn."
            )
        else:
            parts.append(
                "NEXT_SAVE: optional professional memberships (step D, after bio) — ask ONCE if chat history has not already covered it; upsert when they answer or skip without saving junk. "
                "Then preferred_speaking_time (step E) — when the user picks durations, you MUST call upsert_speaker_profile "
                f"with preferred_speaking_time as an array from {_PREFERRED_SPEAKING_TIMES} in this same assistant turn."
            )
    elif not _profile_has_topics(profile):
        parts.append(
            "NEXT_SAVE: topics — when the user names topics, you MUST call upsert_speaker_profile in this same assistant turn with topics (catalog matches) only; do not defer to later steps."
        )
    elif not profile.get("speaking_formats"):
        parts.append(
            "NEXT_SAVE: speaking_formats — upsert valid catalog matches in the same turn as the user's answer (speaking_formats only); do not batch with other catalog fields from prior turns."
        )
    elif not profile.get("delivery_mode"):
        parts.append(
            "NEXT_SAVE: delivery_mode — upsert valid catalog matches in the same turn as the user's answer (delivery_mode only); do not batch with other catalog fields from prior turns."
        )
    elif not profile.get("target_audiences"):
        parts.append(
            "NEXT_SAVE: target_audiences — upsert valid catalog matches in the same turn as the user's answer (target_audiences only); do not batch with optional fields."
        )
    else:
        parts.append(
            "NEXT_SAVE: optional chain (talk_description, then key_takeaways, then past_speaking_examples, video_links, testimonial) — "
            "each user answer MUST trigger upsert_speaker_profile in that same turn for that field; never defer talk_description until key_takeaways or batch multiple optionals in one later upsert."
        )

    return "INTERNAL_ONBOARDING_CHECKPOINT (for you only; do not read aloud): " + " ".join(parts)


def _fixed_list_args_look_mixed(
    step: str,
    tc_args: dict,
    saved_fields: List[str],
    catalog: Optional[Dict[str, List[str]]] = None,
) -> bool:
    """
    True when the field was saved AND at least one raw arg value does not match an allowed name.
    Pure multi-select of only allowed values returns False.
    """
    if step not in (saved_fields or []):
        return False
    raw = (tc_args or {}).get(step)
    if raw is None:
        return False
    if isinstance(raw, str):
        items = [raw.strip()] if raw.strip() else []
    elif isinstance(raw, list):
        items = []
        for x in raw:
            if isinstance(x, dict):
                name = str(x.get("name") or x.get("slug") or "").strip()
            else:
                name = str(x).strip()
            if name:
                items.append(name)
    else:
        return False
    if not items:
        return False

    if step == "preferred_speaking_time":
        for name in items:
            if not _normalize_preferred_speaking_times([name]):
                return True
        return False

    allowed = (catalog or {}).get(step) or []
    allowed_lower = {str(n).strip().lower() for n in allowed if str(n).strip()}
    if not allowed_lower:
        return False
    for name in items:
        if name.lower() not in allowed_lower:
            return True
    return False


def _saved_field_keys_from_doc(doc: dict) -> List[str]:
    out: List[str] = []
    for k, v in doc.items():
        if k in ("_id",):
            continue
        if v is None or v == "" or v == []:
            continue
        if isinstance(v, dict):
            if not (str(v.get("title") or "").strip() or str(v.get("overview") or "").strip()):
                continue
        out.append(k)
    return sorted(out)


def _catalog_field_save_warnings(
    args: dict,
    saved_fields: List[str],
    catalog: Optional[Dict[str, List[str]]],
) -> List[str]:
    """Warn when the model passed catalog values but nothing was persisted."""
    warnings: List[str] = []
    cmap = catalog or {}
    for field in ("topics", "speaking_formats", "delivery_mode", "target_audiences"):
        raw = args.get(field)
        if raw is None:
            continue
        if isinstance(raw, list) and not any(str(x).strip() for x in raw):
            continue
        if field in (saved_fields or []):
            continue
        allowed = cmap.get(field) or []
        sample = ", ".join(allowed[:6])
        if len(allowed) > 6:
            sample += ", ..."
        warnings.append(
            f"{field} was NOT saved: use exact catalog names from the allowed list"
            + (f" (e.g. {sample})" if sample else "")
            + ". Do NOT tell the user this field was saved."
        )
    return warnings


def _upsert_args_nonempty_but_nothing_saved(args: dict, saved_fields: List[str]) -> bool:
    """True when the model passed profile-looking keys but _build_profile_doc produced nothing to write."""
    if saved_fields:
        return False
    for k in PROFILE_FIELDS:
        if k in ("isCompleted",):
            continue
        v = args.get(k)
        if v is None:
            continue
        if isinstance(v, str) and v.strip():
            return True
        if isinstance(v, list) and len(v) > 0:
            return True
        if isinstance(v, dict) and len(v) > 0:
            return True
    return False


def _normalize_past_speaking_examples(raw: Any) -> List[dict]:
    """Coerce tool output to past-speaking dicts: organization_name, event_name, date_month_year only."""
    out: List[dict] = []
    if not isinstance(raw, list):
        return out
    for x in raw:
        if isinstance(x, dict):
            org = str(x.get("organization_name") or "").strip()
            ev = str(x.get("event_name") or "").strip()
            dt = str(x.get("date_month_year") or x.get("date") or "").strip()
            if not org and not ev and not dt:
                rt = str(x.get("relevant_topics") or "").strip()
                aud = str(x.get("audience") or "").strip()
                if rt or aud:
                    org = (rt or aud).strip()
            row = {"organization_name": org, "event_name": ev, "date_month_year": dt}
            if any(row.values()):
                out.append(row)
        elif isinstance(x, str) and x.strip():
            out.append({"organization_name": x.strip(), "event_name": "", "date_month_year": ""})
    return out


def _normalize_professional_memberships(raw: Any) -> List[dict]:
    """Coerce tool output to membership dicts: title, organization, start_date, end_date, is_current."""
    out: List[dict] = []
    if not isinstance(raw, list):
        return out
    for x in raw:
        if isinstance(x, dict):
            title = str(x.get("title") or "").strip()
            org = str(x.get("organization") or "").strip()
            start_date = str(x.get("start_date") or "").strip() or None
            end_date = str(x.get("end_date") or "").strip() or None
            is_current = x.get("is_current")
            if isinstance(is_current, str):
                lowered = is_current.strip().lower()
                if lowered in ("true", "yes", "y", "1"):
                    is_current = True
                elif lowered in ("false", "no", "n", "0"):
                    is_current = False
                else:
                    is_current = None
            elif not isinstance(is_current, bool):
                is_current = None
            row = {
                "title": title,
                "organization": org,
                "start_date": start_date,
                "end_date": end_date,
                "is_current": is_current,
            }
            if title or org or start_date or end_date or is_current is not None:
                out.append(row)
        elif isinstance(x, str) and x.strip():
            out.append(
                {
                    "title": "",
                    "organization": x.strip(),
                    "start_date": None,
                    "end_date": None,
                    "is_current": None,
                }
            )
    return out


class SpeakerProfileChatbotService:
    def __init__(
        self,
        speaker_profile_model,
        speaker_topics_model,
        speaker_target_audience_model,
        delivery_modes_model,
        speaking_formats_model,
        chat_session_model,
        user_model,
    ):
        self.profile_model = speaker_profile_model
        self.topics_model = speaker_topics_model
        self.audience_model = speaker_target_audience_model
        self.delivery_modes_model = delivery_modes_model
        self.speaking_formats_model = speaking_formats_model
        self.chat_session_model = chat_session_model
        self.user_model = user_model
        self._catalog_name_lists: Optional[Dict[str, List[str]]] = None

    async def _user_id_for_new_chatbot_profile(
        self,
        email: str,
        full_name: str,
        jwt_user: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Optional[str], bool]:
        """
        If JWT user is userType user with no stripe_customer_id, use that id (no new user).
        Else if a users row exists for this email, attach it.
        Else only admins may create a new users row; non-admins link to their JWT user id.

        Returns (user_id_or_none, created_new_user_row).
        """
        uid = _jwt_user_id(jwt_user)
        if (
            uid
            and jwt_user.get("userType") in (UserType.USER, UserType.USER.value)
            and not jwt_user.get("stripe_customer_id")
        ):
            return uid, False
        try:
            normalized_email = TypeAdapter(EmailStr).validate_python((email or "").strip())
        except ValidationError:
            logger.warning("Chatbot: invalid email for user row: %s", email)
            return uid, False
        try:
            existing = await self.user_model.get_user({"email": normalized_email})
        except Exception as e:
            logger.warning("Chatbot: user lookup failed for %s: %s", normalized_email, e)
            return uid, False
        if existing is not None and getattr(existing, "id", None) is not None:
            return str(existing.id), False

        if not is_admin_role(jwt_user.get("userType") if jwt_user else None):
            if uid:
                logger.info(
                    "Chatbot: non-admin speaker profile — linking to JWT user %s (no new user row)",
                    uid,
                )
                return uid, False
            logger.warning("Chatbot: non-admin cannot create platform user without JWT user id")
            return None, False

        plain_password = secrets.token_urlsafe(12)
        hashed_password = Utils.hash_password(plain_password)
        fn = _full_name_for_user_account(normalized_email, full_name)
        now = datetime.utcnow()
        user_data_dict = {
            "email": normalized_email,
            "password": hashed_password,
            "fullName": fn,
            "userType": UserType.USER,
            "subscription": default_user_subscription(),
            "createdOn": now,
            "updatedOn": now,
        }
        try:
            inserted_id = await self.user_model.create_user(user_data_dict)
        except Exception as e:
            logger.warning("Chatbot: create_user failed for %s: %s", normalized_email, e)
            return None, False
        send_speaker_credentials_email(normalized_email, fn, plain_password)
        return str(inserted_id), True

    async def _load_catalog_name_lists(self) -> Dict[str, List[str]]:
        """Load allowed option names from DB system catalog only (type=system, incl. legacy rows)."""

        async def sorted_names(model) -> List[str]:
            rows = await model.get_all(doc_type=_CATALOG_TYPE_FOR_LLM)
            names = [str(r.get("name") or "").strip() for r in rows if r and r.get("name")]
            return sorted(set(names), key=str.lower)

        return {
            "topics": await sorted_names(self.topics_model),
            "speaking_formats": await sorted_names(self.speaking_formats_model),
            "delivery_mode": await sorted_names(self.delivery_modes_model),
            "target_audiences": await sorted_names(self.audience_model),
        }

    def _catalog_labels(self) -> Dict[str, List[str]]:
        return self._catalog_name_lists or {
            "topics": [],
            "speaking_formats": [],
            "delivery_mode": [],
            "target_audiences": [],
        }

    async def _resolve_topics(self, topic_names: List[str]) -> List[dict]:
        if not topic_names:
            return []
        allowed = self._catalog_labels().get("topics") or []
        if not allowed:
            allowed = [
                str(r.get("name") or "").strip()
                for r in await self.topics_model.get_all(doc_type=_CATALOG_TYPE_FOR_LLM)
                if r.get("name")
            ]
        filtered = _filter_enum_values(topic_names, allowed)
        if not filtered:
            return []
        return await self.topics_model.get_many_by_names(filtered)

    async def _resolve_target_audiences(self, audience_names: List[str]) -> List[dict]:
        if not audience_names:
            return []
        allowed = self._catalog_labels().get("target_audiences") or []
        if not allowed:
            allowed = [
                str(r.get("name") or "").strip()
                for r in await self.audience_model.get_all(doc_type=_CATALOG_TYPE_FOR_LLM)
                if r.get("name")
            ]
        filtered = _filter_enum_values(audience_names, allowed)
        if not filtered:
            return []
        return await self.audience_model.get_many_by_names(filtered)

    async def _resolve_speaking_formats(self, format_names: List[str]) -> List[dict]:
        """Resolve names to speakingFormats catalog docs {_id, name, slug, type}."""
        if not format_names:
            return []
        allowed = self._catalog_labels().get("speaking_formats") or []
        if not allowed:
            allowed = [
                str(r.get("name") or "").strip()
                for r in await self.speaking_formats_model.get_all(doc_type=_CATALOG_TYPE_FOR_LLM)
                if r.get("name")
            ]
        filtered = _filter_enum_values(format_names, allowed)
        if not filtered:
            return []
        return await self.speaking_formats_model.get_many_by_names(filtered)

    async def _resolve_delivery_modes(self, mode_names: List[str]) -> List[dict]:
        """Resolve names to deliveryModes catalog docs {_id, name, slug, type}."""
        if not mode_names:
            return []
        allowed = self._catalog_labels().get("delivery_mode") or []
        if not allowed:
            allowed = [
                str(r.get("name") or "").strip()
                for r in await self.delivery_modes_model.get_all(doc_type=_CATALOG_TYPE_FOR_LLM)
                if r.get("name")
            ]
        filtered = _filter_enum_values(mode_names, allowed)
        if not filtered:
            return []
        return await self.delivery_modes_model.get_many_by_names(filtered)

    async def _build_profile_doc(self, tool_args: dict) -> dict:
        doc = {}
        email = (tool_args.get("email") or "").strip().lower()
        if email:
            doc["email"] = email
        full_name = (tool_args.get("full_name") or "").strip()
        if full_name:
            doc["full_name"] = full_name
        professional_title = (tool_args.get("professional_title") or "").strip()
        if professional_title:
            doc["professional_title"] = professional_title
        company = (tool_args.get("company") or "").strip()
        if company:
            doc["company"] = company
        topics_raw = tool_args.get("topics")
        if topics_raw and isinstance(topics_raw, list):
            resolved = await self._resolve_topics([str(t).strip() for t in topics_raw])
            if resolved:
                doc["topics"] = resolved
        sf_raw = tool_args.get("speaking_formats")
        if sf_raw and isinstance(sf_raw, list):
            names = [
                str(x.get("name") or x).strip() if isinstance(x, dict) else str(x).strip()
                for x in sf_raw
                if (isinstance(x, dict) and (x.get("name") or x.get("_id"))) or (isinstance(x, str) and x.strip())
            ]
            resolved = await self._resolve_speaking_formats(names)
            if resolved:
                doc["speaking_formats"] = resolved
        dm_raw = tool_args.get("delivery_mode")
        if dm_raw and isinstance(dm_raw, list):
            names = [
                str(x.get("name") or x).strip() if isinstance(x, dict) else str(x).strip()
                for x in dm_raw
                if (isinstance(x, dict) and (x.get("name") or x.get("_id"))) or (isinstance(x, str) and x.strip())
            ]
            resolved = await self._resolve_delivery_modes(names)
            if resolved:
                doc["delivery_mode"] = resolved
        td_raw = tool_args.get("talk_description")
        if td_raw is not None:
            if isinstance(td_raw, dict):
                t_title = str(td_raw.get("title") or "").strip()
                t_over = str(td_raw.get("overview") or "").strip()
                if t_title or t_over:
                    doc["talk_description"] = {"title": t_title, "overview": t_over or t_title}
            elif isinstance(td_raw, str) and td_raw.strip():
                s = td_raw.strip()
                doc["talk_description"] = {"title": s[:200], "overview": s[:2000]}
        audiences_raw = tool_args.get("target_audiences")
        if audiences_raw and isinstance(audiences_raw, list):
            resolved = await self._resolve_target_audiences([str(a).strip() for a in audiences_raw if a])
            if resolved:
                doc["target_audiences"] = resolved
        linkedin = (tool_args.get("linkedin_url") or "").strip()
        if linkedin:
            doc["linkedin_url"] = linkedin
        past = _normalize_past_speaking_examples(tool_args.get("past_speaking_examples"))
        if past:
            doc["past_speaking_examples"] = past
        video = tool_args.get("video_links")
        if isinstance(video, list):
            doc["video_links"] = [str(x).strip() for x in video if x]
        kt_raw = tool_args.get("key_takeaways")
        if kt_raw is not None:
            if isinstance(kt_raw, list):
                kt_list = [str(x).strip() for x in kt_raw if str(x).strip()]
                if kt_list:
                    doc["key_takeaways"] = kt_list
            elif isinstance(kt_raw, str) and kt_raw.strip():
                doc["key_takeaways"] = [kt_raw.strip()]
        tm_raw = tool_args.get("testimonial")
        if tm_raw is not None:
            if isinstance(tm_raw, list):
                tm_list = [str(x).strip() for x in tm_raw if str(x).strip()]
                if tm_list:
                    doc["testimonial"] = tm_list
            elif isinstance(tm_raw, str) and tm_raw.strip():
                doc["testimonial"] = [tm_raw.strip()]
        for k in [
            "name_salutation",
            "bio",
            "twitter",
            "facebook",
            "instagram",
            "address_city",
            "address_state",
            "address_country",
            "phone_number",
        ]:
            v = tool_args.get(k)
            if v is not None and isinstance(v, str):
                doc[k] = v.strip() or None
        pst_raw = tool_args.get("preferred_speaking_time")
        pst_norm = _normalize_preferred_speaking_times(pst_raw)
        if pst_norm:
            doc["preferred_speaking_time"] = pst_norm
        pm = _normalize_professional_memberships(tool_args.get("professional_memberships"))
        if pm:
            doc["professional_memberships"] = pm
        return doc

    def _merge_for_update(self, existing: dict, profile_doc: dict) -> dict:
        """Return only new/changed fields from this upsert (delta), not the full existing profile."""
        delta: Dict[str, Any] = {}
        for k, v in (profile_doc or {}).items():
            if k not in PROFILE_FIELDS or k == "_id":
                continue
            if v is None or v == "" or v == []:
                continue
            delta[k] = v
        return delta

    def _get_fields_to_add_message(self, profile: Optional[dict] = None) -> str:
        """Return list of parameters user can add, as readable text."""
        remaining_mandatory = []
        for f in MANDATORY_FIELDS:
            if f in ("full_name", "email"):
                continue
            if not profile or not bool(profile.get(f)):
                remaining_mandatory.append(MANDATORY_FIELDS_DISPLAY.get(f, f))
        optional_labels = [OPTIONAL_FIELDS_DISPLAY.get(f, f) for f in OPTIONAL_FIELDS]
        parts = []
        if remaining_mandatory:
            parts.append("Required: " + ", ".join(remaining_mandatory))
        if optional_labels:
            parts.append("Optional: " + ", ".join(optional_labels))
        return ". ".join(parts) if parts else "additional profile details"

    def _all_mandatory_filled(self, profile: dict) -> bool:
        """Check if all MANDATORY_FIELDS are filled in the profile."""
        return all(bool(profile.get(f)) for f in MANDATORY_FIELDS)

    async def _set_profile_completed(self, speaker_profile_id: str) -> Optional[dict]:
        """Set isCompleted=True on the profile."""
        return await self.profile_model.update_profile(
            speaker_profile_id,
            {"isCompleted": True},
        )

    async def _sync_user_onboarded(
        self,
        jwt_user: Optional[Dict[str, Any]] = None,
        *,
        user_id: Optional[str] = None,
        profile: Optional[dict] = None,
    ) -> None:
        """Set users.isOnboarded=True when chatbot onboarding is finished."""
        uid = user_id or _jwt_user_id(jwt_user)
        if not uid and profile:
            raw = profile.get("user_id")
            if raw is not None:
                uid = str(raw).strip() or None
        if not uid:
            logger.warning("Chatbot: cannot set isOnboarded — no user_id on jwt/profile")
            return
        try:
            await self.user_model.update_user(
                uid,
                {"isOnboarded": True, "updatedOn": datetime.utcnow()},
            )
            if jwt_user is not None:
                jwt_user["isOnboarded"] = True
            logger.info("Chatbot: set isOnboarded=True for user %s", uid)
        except Exception as e:
            logger.warning("Chatbot: failed to set isOnboarded for user %s: %s", uid, e)

    async def _try_auto_mark_profile_complete(
        self,
        speaker_profile_id: str,
        profile: dict,
        steps_done: List[str],
        *,
        jwt_user: Optional[Dict[str, Any]],
    ) -> Tuple[bool, dict, List[str]]:
        """
        Server-side completion when the user finished the testimonial step.
        Does not rely on the LLM calling mark_profile_complete (it often sends the completion text only).
        Sets speaker_profiles.isCompleted and users.isOnboarded.
        """
        if not may_mark_profile_complete(
            profile,
            steps_done,
            has_profile=True,
            user_turn_answered_last_question=True,
        ):
            return False, profile, steps_done
        if not self._all_mandatory_filled(profile):
            return False, profile, steps_done
        updated = await self._set_profile_completed(speaker_profile_id)
        if updated:
            profile = updated
            profile["_id"] = str(profile.get("_id") or speaker_profile_id)
        else:
            profile["isCompleted"] = True
        await self._sync_user_onboarded(jwt_user, profile=profile)
        return True, profile, steps_done

    async def _execute_upsert(
        self,
        args: dict,
        speaker_profile_id: Optional[str],
        jwt_user: Optional[Dict[str, Any]] = None,
    ) -> dict:
        """Create or update by speaker_profile_id (when provided) or by email."""
        profile_doc = await self._build_profile_doc(args)
        warnings: List[str] = []
        pst_in = args.get("preferred_speaking_time")
        if pst_in is not None:
            norm_pst = _normalize_preferred_speaking_times(pst_in)
            if not norm_pst:
                raw_nonempty = bool(
                    (isinstance(pst_in, str) and pst_in.strip())
                    or (isinstance(pst_in, list) and any(str(x).strip() for x in pst_in))
                )
                if raw_nonempty:
                    warnings.append(
                        "preferred_speaking_time was NOT saved: no value matched the allowed list. "
                        "Re-ask; user must pick from: "
                        + ", ".join(_PREFERRED_SPEAKING_TIMES)
                    )
        if speaker_profile_id:
            saved_fields = _saved_field_keys_from_doc(profile_doc)
            profile = await self.profile_model.get_profile(speaker_profile_id)
            if not profile:
                return {"action": "error", "profile": None, "saved_fields": [], "warnings": warnings}
            # Email/phone are immutable after first save — ignore LLM attempts to replace them.
            if (profile.get("email") or "").strip():
                profile_doc.pop("email", None)
            if (profile.get("phone_number") or "").strip():
                profile_doc.pop("phone_number", None)
            saved_fields = _saved_field_keys_from_doc(profile_doc)
            delta = self._merge_for_update(profile, profile_doc)
            if not delta:
                updated = profile
            else:
                updated = await self.profile_model.update_profile(speaker_profile_id, delta)
                if not updated:
                    return {"action": "error", "profile": None, "saved_fields": [], "warnings": warnings}
                # Recompute saved_fields from what we actually wrote
                saved_fields = _saved_field_keys_from_doc(delta)
            # isCompleted is set only when LLM calls mark_profile_complete (after all questions done)
            cat_warnings = _catalog_field_save_warnings(args, saved_fields, self._catalog_name_lists)
            if cat_warnings:
                warnings = list(warnings) + cat_warnings
            out = {"action": "updated", "profile": updated, "saved_fields": saved_fields, "warnings": warnings}
            if not saved_fields and _upsert_args_nonempty_but_nothing_saved(args, saved_fields):
                out["warnings"] = list(warnings) + [
                    "This upsert had no fields to save. If the user's message contained bio, speaking times, "
                    "topics, or other profile data, you must map that into upsert_speaker_profile arguments "
                    "using EXACT catalog names and call again in this same turn. "
                    "FORBIDDEN: telling the user something was saved when saved_fields is empty."
                ]
            return out
        # Create - require full_name + exactly one valid email + phone
        email = (args.get("email") or "").strip().lower()
        if not email or not _is_valid_email(email):
            return {
                "action": "email_required",
                "profile": None,
                "saved_fields": [],
                "warnings": warnings,
                "reason": "invalid_or_missing_email",
            }
        phone_number = (args.get("phone_number") or "").strip()
        if not phone_number:
            return {
                "action": "phone_required",
                "profile": None,
                "saved_fields": [],
                "warnings": warnings,
                "email": email,
            }
        full_name = (args.get("full_name") or "").strip()
        professional_title = (args.get("professional_title") or "").strip()
        company = (args.get("company") or "").strip()
        if not full_name:
            return {
                "action": "create_blocked",
                "profile": None,
                "missing_fields": ["full_name"],
                "saved_fields": [],
                "warnings": warnings,
            }
        profile_doc["email"] = email
        profile_doc["full_name"] = full_name
        profile_doc["phone_number"] = phone_number
        if professional_title:
            profile_doc["professional_title"] = professional_title
        if company:
            profile_doc["company"] = company
        resolved_user_id, created_new_account = await self._user_id_for_new_chatbot_profile(
            email,
            profile_doc["full_name"],
            jwt_user=jwt_user,
        )
        link_user_id = resolved_user_id or _jwt_user_id(jwt_user)
        created = await self.profile_model.create_chatbot_profile(profile_doc, link_user_id)
        # isOnboarded is set only when the user finishes the last onboarding question (profile complete).
        if created_new_account:
            try_send_welcome_email_on_account_created(
                user_display_name=profile_doc["full_name"],
                account_email=email,
            )
        # isCompleted / isOnboarded are set only when onboarding finishes (testimonial answered).
        create_saved = _saved_field_keys_from_doc(profile_doc)
        return {
            "action": "created",
            "profile": created,
            "saved_fields": create_saved,
            "warnings": warnings,
            "user_id": link_user_id,
        }

    async def process_chat(
        self,
        message: str,
        chat_session_id: Optional[str] = None,
        jwt_user: Optional[Dict[str, Any]] = None,
    ) -> dict:
        """
        Flow:
        - Pre-profile: collect full_name, title, company in chat; then exactly one email (+ resolve multi-email /
          reject non-email); then phone if missing; upsert creates with full_name + email + phone.
        - After create: email/phone are locked; location → social → bio → optional memberships → preferred speaking time → catalog fields → remaining optionals; session stores speaker_profile_id.
        """
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return {
                "assistant_message": "Service is temporarily unavailable.",
                "action": None,
                "speaker_profile_id": None,
                "chat_session_id": chat_session_id,
                "profile_snapshot": None,
            }

        client = OpenAI(api_key=api_key)
        self._catalog_name_lists = await self._load_catalog_name_lists()
        catalog = self._catalog_name_lists

        session = None
        speaker_profile_id = None
        profile = None
        history: List[Dict[str, Any]] = []

        if chat_session_id:
            session = await self.chat_session_model.get_by_id(chat_session_id)
            if session:
                speaker_profile_id = (session.get("speaker_profile_id") or "").strip() or None
                if speaker_profile_id:
                    profile = await self.profile_model.get_profile(speaker_profile_id)
                    if profile:
                        profile["_id"] = str(profile["_id"])
                conv = session.get("conversation") or []
                history = [{"role": m.get("role", "user"), "content": m.get("content", "")} for m in conv]

        messages = [*history, {"role": "user", "content": message or ""}]

        has_profile = bool(speaker_profile_id and profile)
        steps_done: List[str] = list((session or {}).get("onboarding_steps_done") or [])
        step_at_turn_start = derive_expected_step(profile, steps_done, has_profile=has_profile)
        user_turn_answered_last_question = has_profile and step_at_turn_start == "testimonial"
        user_skipped_optional_step = (
            has_profile
            and step_at_turn_start in SKIPPABLE_STEPS
            and detect_skip_intent(message or "")
        )

        if user_skipped_optional_step:
            steps_done = merge_steps_done(steps_done, [step_at_turn_start])
        expected_step = derive_expected_step(profile, steps_done, has_profile=has_profile)

        def _ser(o):
            if hasattr(o, "isoformat"):
                return o.isoformat()
            if isinstance(o, dict):
                return {k: _ser(v) for k, v in o.items()}
            if isinstance(o, list):
                return [_ser(x) for x in o]
            return str(o) if hasattr(o, "hex") else o

        profile_snapshot_fields = (
            "full_name",
            "professional_title",
            "company",
            "email",
            "phone_number",
            "address_city",
            "address_state",
            "address_country",
            "bio",
            "preferred_speaking_time",
            "topics",
            "target_audiences",
            "speaking_formats",
            "delivery_mode",
            "talk_description",
            "key_takeaways",
            "linkedin_url",
            "professional_memberships",
            "twitter",
            "facebook",
            "instagram",
            "past_speaking_examples",
            "video_links",
            "testimonial",
        )
        profile_json = "{}"
        if profile:
            profile_json = json.dumps(
                {k: _ser(profile.get(k)) for k in profile_snapshot_fields if profile.get(k) is not None},
                default=str,
            )

        checkpoint_line = (
            build_checkpoint_for_prompt(
                profile,
                steps_done,
                has_profile=has_profile,
                preferred_speaking_times=_PREFERRED_SPEAKING_TIMES,
            )
            if profile
            else ""
        )
        pre_create_subphase = (
            derive_pre_create_subphase(history, message) if not has_profile else None
        )

        if (
            not has_profile
            and pre_create_subphase == PRE_CREATE_PROMPT_WELCOME
            and (message or "").strip()
        ):
            identity = extract_pre_create_identity(client, message)
            if identity.get("full_name"):
                assistant_content = build_identity_welcome_reply(identity["full_name"])
                chunk = [
                    {"role": "user", "content": message},
                    {"role": "assistant", "content": assistant_content},
                ]
                if session:
                    await self.chat_session_model.append_messages(chat_session_id, chunk)
                    await self.chat_session_model.update_pending_identity(chat_session_id, identity)
                    chat_session_id_out = chat_session_id
                else:
                    new_sess = await self.chat_session_model.create_session(
                        speaker_profile_id="", messages=chunk
                    )
                    chat_session_id_out = new_sess["_id"]
                    await self.chat_session_model.update_pending_identity(
                        chat_session_id_out, identity
                    )
                self._catalog_name_lists = None
                return {
                    "assistant_message": assistant_content,
                    "action": None,
                    "speaker_profile_id": None,
                    "chat_session_id": chat_session_id_out,
                    "profile_snapshot": None,
                }

        pending_identity = (session or {}).get("pending_identity") if not has_profile else None

        # Pre-create contact guards: multiple emails / non-email chatter before profile exists.
        if not has_profile and pre_create_subphase in (PRE_CREATE_POST_WELCOME, PRE_CREATE_READY):
            emails_in_msg = _extract_emails_from_text(message or "")
            if len(emails_in_msg) >= 2:
                listed = ", ".join(emails_in_msg)
                assistant_content = (
                    f"I found more than one email ({listed}). "
                    "Which one should I use for your speaker profile?"
                )
                chunk = [
                    {"role": "user", "content": message},
                    {"role": "assistant", "content": assistant_content},
                ]
                if session:
                    await self.chat_session_model.append_messages(chat_session_id, chunk)
                    chat_session_id_out = chat_session_id
                else:
                    new_sess = await self.chat_session_model.create_session(
                        speaker_profile_id="", messages=chunk
                    )
                    chat_session_id_out = new_sess["_id"]
                self._catalog_name_lists = None
                return {
                    "assistant_message": assistant_content,
                    "action": None,
                    "speaker_profile_id": None,
                    "chat_session_id": chat_session_id_out,
                    "profile_snapshot": None,
                }
            if (
                pre_create_subphase == PRE_CREATE_POST_WELCOME
                and not emails_in_msg
                and (message or "").strip()
            ):
                if _looks_like_phone_number(message):
                    assistant_content = (
                        "Thanks—I've noted your phone number. "
                        "Could you also share the email address you'd like to use for your speaker profile?"
                    )
                else:
                    assistant_content = (
                        "That doesn't look like an email address. "
                        "Please reply with a proper email (for example, name@company.com)."
                    )
                chunk = [
                    {"role": "user", "content": message},
                    {"role": "assistant", "content": assistant_content},
                ]
                if session:
                    await self.chat_session_model.append_messages(chat_session_id, chunk)
                    chat_session_id_out = chat_session_id
                else:
                    new_sess = await self.chat_session_model.create_session(
                        speaker_profile_id="", messages=chunk
                    )
                    chat_session_id_out = new_sess["_id"]
                self._catalog_name_lists = None
                return {
                    "assistant_message": assistant_content,
                    "action": None,
                    "speaker_profile_id": None,
                    "chat_session_id": chat_session_id_out,
                    "profile_snapshot": None,
                }

        system = build_simple_system_prompt(
            has_profile=has_profile,
            profile_json=profile_json,
            speaker_profile_id=speaker_profile_id,
            expected_step=expected_step,
            catalog=catalog,
            checkpoint_line=checkpoint_line,
            pre_create_subphase=pre_create_subphase,
        )
        if has_profile:
            system += (
                "\n\n"
                + _INVALID_FIXED_LIST_GUIDANCE
                + "\n\n"
                + _FIXED_LIST_ADVANCE_AFTER_OFF_LIST
                + "\n\n"
                + _FIXED_LIST_PARTIAL_OR_MIXED_FLOW
                + "\n\n"
                + _FIXED_LIST_USER_FACING_TRUTH
            )
        system += (
            "\n\nCOMPLETION MESSAGE (verbatim, only after user replies to testimonial and mark_profile_complete succeeds):\n"
            + _PROFILE_COMPLETION_MESSAGE
        )
        if user_skipped_optional_step:
            system += (
                f"\n\nSKIP THIS TURN: The user skipped optional step '{step_at_turn_start}' "
                "(valid onboarding answer—not off-topic). Briefly acknowledge with their first name, "
                "then ask the next step question verbatim below. Do NOT call upsert_speaker_profile unless they provided data. "
                'FORBIDDEN: "I can only help with your SpeakerPitcher profile onboarding right now."'
            )
        if pending_identity:
            system += (
                "\n\nSTORED IDENTITY (include on upsert_speaker_profile when creating the profile): "
                + json.dumps(pending_identity)
            )
        if has_profile and profile:
            locked_bits = []
            if (profile.get("email") or "").strip():
                locked_bits.append("email")
            if (profile.get("phone_number") or "").strip():
                locked_bits.append("phone_number")
            if locked_bits:
                system += (
                    "\n\nCONTACT LOCK (CRITICAL): These fields are already saved and MUST NOT be changed: "
                    + ", ".join(locked_bits)
                    + ". Never pass them in upsert_speaker_profile. "
                    "If the user types another email or phone while answering a later question, ignore that contact text—"
                    "do not replace the saved values and do not acknowledge changing them."
                )

        tools = [_build_upsert_tool(speaker_profile_id), _build_get_allowed_values_tool()]
        if speaker_profile_id:
            tools.append(_build_mark_profile_complete_tool(speaker_profile_id))
        chat_messages = [{"role": "system", "content": system}, *messages]
        tool_results = []
        profile_marked_complete = False
        # First completion must call upsert when the user just answered a profile step (stops text-only "saved" lies).
        force_upsert_first = bool(
            has_profile
            and (message or "").strip()
            and expected_step != CREATE_STEP
            and not user_skipped_optional_step
        )
        for loop_i in range(6):
            tool_choice: Any = "auto"
            if loop_i == 0 and force_upsert_first:
                tool_choice = {"type": "function", "function": {"name": "upsert_speaker_profile"}}
            completion = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=chat_messages,
                tools=tools,
                tool_choice=tool_choice,
                temperature=0.25,
                timeout=30,
            )
            msg = completion.choices[0].message
            if not msg:
                break
            asst = {"role": "assistant", "content": msg.content or ""}
            if msg.tool_calls:
                asst["tool_calls"] = [
                    {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"}}
                    for tc in msg.tool_calls
                ]
            chat_messages.append(asst)
            tcs = msg.tool_calls or []
            if not tcs:
                break
            for tc in tcs:
                try:
                    tc_args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    tc_args = {}
                if tc.function.name == "get_allowed_values":
                    vt = (tc_args.get("value_type") or "").strip().lower()
                    cmap = self._catalog_name_lists or {}
                    allowed = cmap.get(vt, [])
                    chat_messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps({"allowed_values": allowed}),
                    })
                    continue
                if tc.function.name == "mark_profile_complete":
                    spid = (tc_args.get("speaker_profile_id") or "").strip() or speaker_profile_id
                    allow_complete = may_mark_profile_complete(
                        profile,
                        steps_done,
                        has_profile=bool(spid and profile),
                        user_turn_answered_last_question=user_turn_answered_last_question,
                    )
                    if spid and allow_complete and self._all_mandatory_filled(profile or {}):
                        updated = await self._set_profile_completed(spid)
                        profile_marked_complete = True
                        if updated:
                            profile = updated
                            profile["_id"] = str(profile.get("_id") or spid)
                        elif profile:
                            profile["isCompleted"] = True
                        await self._sync_user_onboarded(jwt_user, profile=profile)
                    chat_messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps({
                            "success": profile_marked_complete,
                            "user_message_must_be_exactly": _PROFILE_COMPLETION_MESSAGE
                            if profile_marked_complete
                            else None,
                            "error": None
                            if profile_marked_complete
                            else "Call mark_profile_complete only after the user replies to the testimonial question (last step).",
                        }),
                    })
                    continue
                if tc.function.name != "upsert_speaker_profile":
                    continue
                if not speaker_profile_id:
                    tc_args = _merge_pending_identity_into_args(tc_args, pending_identity)
                # Always prefer the session/known profile id so updates never miss the document.
                if speaker_profile_id:
                    tc_args["speaker_profile_id"] = speaker_profile_id
                spid = (tc_args.get("speaker_profile_id") or "").strip() or None
                result = await self._execute_upsert(
                    tc_args,
                    spid or speaker_profile_id,
                    jwt_user,
                )
                tool_results.append(result)
                if result.get("action") == "created" and chat_session_id:
                    await self.chat_session_model.update_pending_identity(chat_session_id, None)
                if result.get("profile"):
                    profile = result["profile"]
                    profile["_id"] = str(profile["_id"])
                saved = result.get("saved_fields") or []
                if saved:
                    steps_done = merge_steps_done(
                        steps_done,
                        steps_from_saved_fields(saved),
                    )
                # Off-list / zero-match fixed-list answers: mark step done and advance (add-later messaging).
                fixed_list_steps = set(CATALOG_STEPS) | {"preferred_speaking_time"}
                catalog_off_list = (
                    result.get("action") == "updated"
                    and expected_step in fixed_list_steps
                    and expected_step in (tc_args or {})
                    and expected_step not in steps_from_saved_fields(saved)
                )
                catalog_mixed = (
                    result.get("action") == "updated"
                    and expected_step in fixed_list_steps
                    and expected_step in (tc_args or {})
                    and expected_step in steps_from_saved_fields(saved)
                    and _fixed_list_args_look_mixed(
                        expected_step, tc_args, saved, self._catalog_name_lists
                    )
                )
                if result.get("action") == "created":
                    steps_done = merge_steps_done(steps_done, [CREATE_STEP])
                if (
                    step_at_turn_start in SKIPPABLE_STEPS
                    and detect_skip_intent(message or "")
                    and step_at_turn_start not in steps_done
                ):
                    steps_done = merge_steps_done(steps_done, [step_at_turn_start])
                if result.get("action") == "created" and profile and profile.get("_id"):
                    speaker_profile_id = str(profile["_id"])
                    has_profile = True
                    # Link session immediately so later turns (and same-turn follow-ups) update this profile.
                    if chat_session_id:
                        await self.chat_session_model.update_speaker_profile_id(
                            chat_session_id, speaker_profile_id
                        )
                    # Switch tool schema to UPDATE mode for any further tool calls this turn.
                    tools = [
                        _build_upsert_tool(speaker_profile_id),
                        _build_get_allowed_values_tool(),
                        _build_mark_profile_complete_tool(speaker_profile_id),
                    ]
                tr_payload: Dict[str, Any] = {
                    "action": result.get("action"),
                    "profile_id": str(profile.get("_id", "")) if profile else "",
                    "speaker_profile_id": str(profile.get("_id", "")) if profile else "",
                    "saved_fields": result.get("saved_fields") or [],
                    "warnings": result.get("warnings") or [],
                    "reminder": (
                        "If the user message in this turn contained profile answers, ensure saved_fields reflects them; "
                        "otherwise call upsert_speaker_profile again in this same multi-step turn with the correct fields. "
                        "FORBIDDEN: telling the user something was saved when saved_fields is empty."
                    ),
                }
                if not (result.get("saved_fields") or []) and result.get("action") in ("updated", "created"):
                    tr_payload["reminder"] = (
                        "NOTHING was saved in this upsert (saved_fields is empty). "
                        "Do NOT tell the user you saved, added, or updated any profile field. "
                        "Call upsert_speaker_profile again with the correct fields for this step "
                        f"(include speaker_profile_id={speaker_profile_id or (profile or {}).get('_id')!r})."
                    )
                if catalog_off_list:
                    # Advance past this step even with zero saves so the next question can show.
                    steps_done = merge_steps_done(steps_done, [expected_step])
                    tr_payload["catalog_off_list"] = True
                    tr_payload["reminder"] = (
                        f"No exact allowed match for '{expected_step}'. "
                        "In your text reply write ONLY a short warm line (first name when known): "
                        "those choices aren't on the allowed list, but they can add them later from their speaker profile. "
                        "FORBIDDEN: re-asking this step; asking 'would you like to continue?'; claiming you saved their wording; "
                        "pasting the next question or bullets (the server appends the NEXT step)."
                    )
                elif catalog_mixed:
                    tr_payload["catalog_mixed"] = True
                    tr_payload["reminder"] = (
                        f"Mixed answer for '{expected_step}': some values were saved ({', '.join(saved)}). "
                        "In your text reply: (1) briefly confirm ONLY the exact allowed names that were saved; "
                        "(2) say the other named items aren't on the list but they can add them later from their speaker profile. "
                        "FORBIDDEN: asking 'continue?'; re-asking this step; pasting next question/bullets (server appends NEXT step)."
                    )
                if result.get("missing_fields"):
                    tr_payload["missing_fields"] = result["missing_fields"]
                chat_messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(tr_payload),
                })

        # Testimonial / optional skips: ensure step is recorded, then complete in DB without relying on LLM tool.
        if (
            not profile_marked_complete
            and user_turn_answered_last_question
            and has_profile
            and speaker_profile_id
            and profile
        ):
            if step_at_turn_start in SKIPPABLE_STEPS and detect_skip_intent(message or ""):
                steps_done = merge_steps_done(steps_done, [step_at_turn_start])
            profile_marked_complete, profile, steps_done = await self._try_auto_mark_profile_complete(
                str(speaker_profile_id),
                profile,
                steps_done,
                jwt_user=jwt_user,
            )

        action = None
        if tool_results:
            action = tool_results[-1].get("action")
            if profile is None and tool_results[-1].get("profile"):
                profile = tool_results[-1]["profile"]
                profile["_id"] = str(profile["_id"])

        assistant_content = ""
        last = chat_messages[-1] if chat_messages else {}
        if isinstance(last, dict) and last.get("role") == "assistant":
            assistant_content = (last.get("content") or "").strip()
        if not assistant_content or last.get("role") == "tool":
            if action == "email_required":
                assistant_content = (
                    "That doesn't look like a valid email address. "
                    "Please share one proper email (for example, name@company.com), "
                    "and your phone number if you haven't already."
                )
            elif action == "phone_required":
                email_hint = ""
                if tool_results:
                    email_hint = (tool_results[-1].get("email") or "").strip()
                if email_hint:
                    assistant_content = (
                        f"Thanks—I've got your email ({email_hint}). "
                        "Could you share your phone number next so I can continue with your speaker profile?"
                    )
                else:
                    assistant_content = (
                        "I still need your phone number before I can create your speaker profile. "
                        "What's the best number to reach you?"
                    )
            elif action == "create_blocked":
                miss = tool_results[-1].get("missing_fields") if tool_results else None
                if miss:
                    pretty = ", ".join(str(m).replace("_", " ") for m in miss)
                    assistant_content = (
                        f"To create your profile I still need: {pretty}. Could you share that?"
                    )
                else:
                    assistant_content = (
                        "We're almost there—I need your professional name and a valid email "
                        "before I can create your profile. What's still missing?"
                    )
            elif action == "created" and profile:
                full_name = (profile.get("full_name") or "").strip()
                first_name = _first_name_from_full_name(full_name) or "you"
                email = (profile.get("email") or "").strip() or ""
                prompt = (
                    f"Briefly welcome {first_name} (first name only) and confirm their speaker profile was started"
                    + (f" ({email})" if email else "")
                    + ". Then ask for their location using EXACTLY this question text (verbatim), after one short friendly ack that uses their first name: "
                    + repr(_CHAT_LOCATION_QUESTION)
                    + " FORBIDDEN: asking about topics, speaking formats, delivery, or audiences in this message. "
                    "STRICTLY FORBIDDEN: any mention of creating a user account, login, password, sign-in, credentials, temporary password, or that they received an email about an account—only discuss the speaker profile onboarding."
                )
                try:
                    s = client.chat.completions.create(
                        model="gpt-4o-mini",
                        messages=chat_messages + [{"role": "user", "content": prompt}],
                        temperature=0.3,
                        timeout=15,
                    )
                    assistant_content = (s.choices[0].message.content or "").strip()
                except Exception:
                    pass
                if not assistant_content:
                    assistant_content = (
                        f"Great, {first_name}—we've started your speaker profile"
                        + (f" ({email})" if email else "")
                        + ". "
                        + _CHAT_LOCATION_QUESTION
                    )
            else:
                if action == "created":
                    prompt = (
                        "Briefly acknowledge progress on their speaker profile only. "
                        "FORBIDDEN: any mention of user account, login, password, sign-in, or credentials email."
                    )
                elif action == "updated":
                    prompt = "Briefly tell the user what was updated."
                else:
                    prompt = "How can I assist you today to create a speaker profile? I'll need your email address to get started."
                if not assistant_content:
                    try:
                        s = client.chat.completions.create(
                            model="gpt-4o-mini",
                            messages=chat_messages + [{"role": "user", "content": prompt}],
                            temperature=0.3,
                            timeout=15,
                        )
                        assistant_content = (s.choices[0].message.content or "").strip()
                    except Exception:
                        pass
                if not assistant_content:
                    if profile_marked_complete and user_turn_answered_last_question:
                        assistant_content = _PROFILE_COMPLETION_MESSAGE
                    elif not profile_marked_complete:
                        assistant_content = (
                            "Your speaker profile is off to a good start!"
                            if action == "created"
                            else (
                                "Your profile has been updated."
                                if action == "updated"
                                else "How can I assist you today to create a speaker profile? I'll need your email address to get started."
                            )
                        )

        if profile_marked_complete and user_turn_answered_last_question:
            assistant_content = _PROFILE_COMPLETION_MESSAGE
        elif profile_marked_complete:
            profile_marked_complete = False
        elif (
            user_turn_answered_last_question
            and assistant_content
            and "successfully completed" in assistant_content.lower()
            and has_profile
            and speaker_profile_id
            and profile
        ):
            if step_at_turn_start in SKIPPABLE_STEPS and detect_skip_intent(message or ""):
                steps_done = merge_steps_done(steps_done, [step_at_turn_start])
            profile_marked_complete, profile, steps_done = await self._try_auto_mark_profile_complete(
                str(speaker_profile_id),
                profile,
                steps_done,
                jwt_user=jwt_user,
            )
            if profile_marked_complete:
                assistant_content = _PROFILE_COMPLETION_MESSAGE

        if assistant_content and (
            speakerpitcher_welcome_already_sent(history)
            or has_profile
            or pre_create_subphase in (PRE_CREATE_POST_WELCOME, PRE_CREATE_READY)
        ):
            assistant_content = strip_duplicate_speakerpitcher_welcome(assistant_content)

        # Catalog questions: server always owns question + bullets + footer
        if has_profile and not profile_marked_complete:
            assistant_content = ensure_catalog_list_in_reply(
                has_profile=has_profile,
                profile_marked_complete=profile_marked_complete,
                profile=profile,
                steps_done=steps_done,
                assistant_content=assistant_content or "",
                catalog=catalog,
            )

        # ChatSession: create if new, else append
        chunk = [{"role": "user", "content": message or ""}, {"role": "assistant", "content": assistant_content}]
        if session:
            await self.chat_session_model.append_messages(chat_session_id, chunk)
            chat_session_id_out = chat_session_id
            await self.chat_session_model.update_onboarding_steps_done(chat_session_id, steps_done)
            # Always attach profile id once we have one (do not require last action == "created").
            if profile and profile.get("_id"):
                existing_spid = (session.get("speaker_profile_id") or "").strip()
                if not existing_spid:
                    await self.chat_session_model.update_speaker_profile_id(
                        chat_session_id, str(profile["_id"])
                    )
        else:
            spid_for_session = str(profile.get("_id")) if profile and profile.get("_id") else ""
            new_sess = await self.chat_session_model.create_session(
                speaker_profile_id=spid_for_session, messages=chunk
            )
            chat_session_id_out = new_sess["_id"]
            if steps_done:
                await self.chat_session_model.update_onboarding_steps_done(
                    chat_session_id_out, steps_done
                )

        # Set action = "completed" only after user answered testimonial and profile was marked complete
        if profile_marked_complete and user_turn_answered_last_question:
            action = "completed"

        self._catalog_name_lists = None
        return {
            "assistant_message": assistant_content,
            "action": action,
            "speaker_profile_id": profile.get("_id") if profile else None,
            "chat_session_id": chat_session_id_out,
            "profile_snapshot": profile,
        }
