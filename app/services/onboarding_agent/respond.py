"""GenerateResponse — assistant reply builder (server owns next question; LLM polishes copy)."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.services.speaker_profile_chatbot_steps import (
    CATALOG_STEPS,
    OPTION_LIST_STEPS,
    PRE_CREATE_ASK_IDENTITY,
    PRE_CREATE_POST_WELCOME,
    PRE_CREATE_PROMPT_WELCOME,
    PRE_CREATE_READY,
    SKIPPABLE_STEPS,
    STEP_UPSERT_FIELDS,
    build_identity_welcome_reply,
    build_step_user_message,
    ensure_catalog_list_in_reply,
    looks_like_prompt_injection,
    speakerpitcher_welcome_already_sent,
    strip_duplicate_speakerpitcher_welcome,
)

logger = logging.getLogger(__name__)

_REPLY_MODEL = "gpt-4o-mini"

_PROFILE_COMPLETION_MESSAGE = (
    "Your speaker profile has been successfully completed. "
    "You may now close this window and review your profile at your convenience.\n\n"
    "Upon closing this window, you will receive an email containing your login credentials "
    "to access and review your profile online.\n\n"
    "Thank you."
)

_IDENTITY_EMAIL_PHONE_QUESTION = "Could you please provide your email and phone number?"
_IDENTITY_PHONE_ONLY_QUESTION = "Could you please share your phone number?"
_IDENTITY_EMAIL_ONLY_QUESTION = "Could you please share your email address?"
_IDENTITY_FULL_QUESTION = "Please share your professional name, title, and company."

_CONTACT_QUESTION_IDS = frozenset(
    {
        PRE_CREATE_PROMPT_WELCOME,
        PRE_CREATE_POST_WELCOME,
        PRE_CREATE_READY,
        "create_profile",
    }
)

_PENDING_CONTEXT_KEYS = (
    "full_name",
    "professional_title",
    "company",
    "email",
    "phone_number",
)

_PROFILE_CONTEXT_KEYS = (
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
    "website_url",
    "twitter",
    "facebook",
    "instagram",
)

_FIELD_ASK_LABELS = {
    "full_name": "professional name",
    "professional_title": "title",
    "company": "company",
    "email": "email",
    "phone_number": "phone number",
    "address_city": "city",
    "address_state": "state or province",
    "address_country": "country",
    "linkedin_url": "LinkedIn",
    "twitter": "X/Twitter",
    "facebook": "Facebook",
    "instagram": "Instagram",
}

_RECENT_USER_INPUT_LIMIT = 5
_RECENT_USER_INPUT_MAX_CHARS = 500


def completion_message() -> str:
    return _PROFILE_COMPLETION_MESSAGE


def first_name(full_name: str) -> str:
    raw = (full_name or "").strip()
    if not raw:
        return ""
    before = raw.split(",")[0].strip()
    parts = before.split()
    return parts[0] if parts else ""


def _nonempty_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, dict)):
        return ""
    return str(value).strip()


def recent_valid_user_inputs(
    history: Optional[List[Dict[str, Any]]],
    *,
    limit: int = _RECENT_USER_INPUT_LIMIT,
    max_chars: int = _RECENT_USER_INPUT_MAX_CHARS,
) -> List[str]:
    """
    Last N non-empty user messages from history (oldest→newest).
    Skips prompt-injection-looking text; truncates long messages.
    """
    collected: List[str] = []
    for msg in history or []:
        if (msg or {}).get("role") != "user":
            continue
        text = _nonempty_str((msg or {}).get("content"))
        if not text:
            continue
        if looks_like_prompt_injection(text):
            continue
        if len(text) > max_chars:
            text = text[:max_chars].rstrip() + "…"
        collected.append(text)
    if limit <= 0:
        return []
    return collected[-limit:]


def _contact_ask_only(pending_identity: Optional[Dict[str, Any]]) -> str:
    """Return 'phone' | 'email' | 'both' based on pending contact fields."""
    pending = pending_identity if isinstance(pending_identity, dict) else {}
    email = _nonempty_str(pending.get("email"))
    phone = _nonempty_str(pending.get("phone_number"))
    if email and not phone:
        return "phone"
    if phone and not email:
        return "email"
    return "both"


def _contact_question_text(pending_identity: Optional[Dict[str, Any]]) -> str:
    ask = _contact_ask_only(pending_identity)
    if ask == "phone":
        return _IDENTITY_PHONE_ONLY_QUESTION
    if ask == "email":
        return _IDENTITY_EMAIL_ONLY_QUESTION
    return _IDENTITY_EMAIL_PHONE_QUESTION


def _step_fields_for_partial(question_id: str) -> List[str]:
    if question_id == PRE_CREATE_ASK_IDENTITY:
        return ["full_name", "professional_title", "company"]
    if question_id in _CONTACT_QUESTION_IDS:
        return ["email", "phone_number"]
    if question_id == "location":
        return sorted(STEP_UPSERT_FIELDS.get("location") or [])
    if question_id == "social":
        return sorted(STEP_UPSERT_FIELDS.get("social") or [])
    return []


def _join_field_labels(fields: List[str]) -> str:
    labels = [_FIELD_ASK_LABELS.get(f, f.replace("_", " ")) for f in fields]
    if not labels:
        return ""
    if len(labels) == 1:
        return labels[0]
    if len(labels) == 2:
        return f"{labels[0]} and {labels[1]}"
    return ", ".join(labels[:-1]) + f", and {labels[-1]}"


def _identity_question_text(pending_identity: Optional[Dict[str, Any]]) -> str:
    pending = pending_identity if isinstance(pending_identity, dict) else {}
    fields = _step_fields_for_partial(PRE_CREATE_ASK_IDENTITY)
    missing = [f for f in fields if not _nonempty_str(pending.get(f))]
    if not missing or len(missing) == len(fields):
        return _IDENTITY_FULL_QUESTION
    return f"Could you also share your {_join_field_labels(missing)}?"


def _location_question_text(profile: Optional[dict]) -> str:
    prof = profile if isinstance(profile, dict) else {}
    fields = _step_fields_for_partial("location")
    missing = [f for f in fields if not _nonempty_str(prof.get(f))]
    if not missing or len(missing) == len(fields):
        return build_step_user_message("location", None)
    if missing == ["address_state"]:
        return "What state or province are you based in?"
    if missing == ["address_city"]:
        return "What city are you based in?"
    if missing == ["address_country"]:
        return "What country are you based in?"
    return f"Could you also share your {_join_field_labels(missing)}?"


def _social_question_text(profile: Optional[dict]) -> str:
    prof = profile if isinstance(profile, dict) else {}
    fields = _step_fields_for_partial("social")
    have = [f for f in fields if _nonempty_str(prof.get(f))]
    missing = [f for f in fields if not _nonempty_str(prof.get(f))]
    if not have or not missing:
        return build_step_user_message("social", None)
    return (
        "Do you have any other professional social URLs to add "
        f"(e.g. {_join_field_labels(missing)})? You can also say skip."
    )


def _compute_step_partial(
    *,
    question_id: str,
    known: Dict[str, str],
) -> Optional[Dict[str, Any]]:
    fields = _step_fields_for_partial(question_id)
    if len(fields) < 2:
        return None
    have = [f for f in fields if known.get(f)]
    missing = [f for f in fields if not known.get(f)]
    if not have:
        return None
    return {
        "have": have,
        "missing": missing,
        "ask_only": missing if missing else list(fields),
    }


def build_reply_user_context(
    *,
    pending_identity: Optional[Dict[str, Any]] = None,
    profile: Optional[dict] = None,
    next_question_id: str = "",
) -> Dict[str, Any]:
    """
    Compact known-user snapshot for reply polish.
    Omits empty values; adds step_partial / contact hints for multi-field steps.
    """
    known: Dict[str, str] = {}
    pending = pending_identity if isinstance(pending_identity, dict) else {}
    for key in _PENDING_CONTEXT_KEYS:
        val = _nonempty_str(pending.get(key))
        if val:
            known[key] = val

    prof = profile if isinstance(profile, dict) else {}
    for key in _PROFILE_CONTEXT_KEYS:
        if key in known:
            continue
        val = _nonempty_str(prof.get(key))
        if val:
            known[key] = val

    ctx: Dict[str, Any] = {"known": known}
    if next_question_id in _CONTACT_QUESTION_IDS:
        email = _nonempty_str(pending.get("email")) or _nonempty_str(prof.get("email"))
        phone = _nonempty_str(pending.get("phone_number")) or _nonempty_str(
            prof.get("phone_number")
        )
        have_email = bool(email)
        have_phone = bool(phone)
        if have_email and not have_phone:
            ask_only = "phone"
        elif have_phone and not have_email:
            ask_only = "email"
        else:
            ask_only = "both"
        ctx["contact"] = {
            "have_email": have_email,
            "have_phone": have_phone,
            "ask_only": ask_only,
        }

    step_partial = _compute_step_partial(question_id=next_question_id or "", known=known)
    if step_partial:
        ctx["step_partial"] = step_partial
    return ctx


def format_reply_user_context_lines(ctx: Dict[str, Any]) -> List[str]:
    """Labeled lines for the LLM prompt."""
    lines: List[str] = []
    known = ctx.get("known") if isinstance(ctx.get("known"), dict) else {}
    if known:
        for key, val in known.items():
            lines.append(f"{key}={val}")
    else:
        lines.append("(none)")
    contact = ctx.get("contact")
    if isinstance(contact, dict):
        lines.append(
            "contact_ask_only="
            f"{contact.get('ask_only')} "
            f"(have_email={contact.get('have_email')}, have_phone={contact.get('have_phone')})"
        )
    step_partial = ctx.get("step_partial")
    if isinstance(step_partial, dict):
        lines.append(
            "step_partial "
            f"have={step_partial.get('have')} "
            f"missing={step_partial.get('missing')} "
            f"ask_only={step_partial.get('ask_only')}"
        )
    return lines


def build_next_question_text(
    question_id: str,
    catalog: Optional[Dict[str, List[str]]],
    *,
    history: Optional[List[Dict[str, Any]]] = None,
    pending_identity: Optional[Dict[str, Any]] = None,
    profile: Optional[dict] = None,
    welcome_sent: bool = False,
) -> str:
    if not question_id:
        return ""
    if question_id == PRE_CREATE_ASK_IDENTITY:
        return _identity_question_text(pending_identity)
    if question_id == PRE_CREATE_PROMPT_WELCOME:
        full_name = (pending_identity or {}).get("full_name") or ""
        already = welcome_sent or speakerpitcher_welcome_already_sent(history or [])
        contact_q = _contact_question_text(pending_identity)
        if full_name and not already:
            # Welcome once, then ask only for still-missing contact fields
            welcome = build_identity_welcome_reply(full_name)
            # build_identity_welcome_reply embeds dual ask — replace with context-aware ask
            dual = _IDENTITY_EMAIL_PHONE_QUESTION
            if dual in welcome and contact_q != dual:
                return welcome.replace(dual, contact_q)
            return welcome
        return contact_q
    if question_id in (PRE_CREATE_POST_WELCOME, PRE_CREATE_READY, "create_profile"):
        return _contact_question_text(pending_identity)
    if question_id == "location":
        return _location_question_text(profile)
    if question_id == "social":
        return _social_question_text(profile)
    return build_step_user_message(question_id, catalog)


def compose_reply(
    *,
    ack: str,
    next_question_id: str,
    catalog: Optional[Dict[str, List[str]]],
    history: Optional[List[Dict[str, Any]]] = None,
    pending_identity: Optional[Dict[str, Any]] = None,
    profile: Optional[dict] = None,
    steps_done: Optional[List[str]] = None,
    has_profile: bool = False,
    profile_marked_complete: bool = False,
    welcome_sent: bool = False,
) -> str:
    q = build_next_question_text(
        next_question_id,
        catalog,
        history=history,
        pending_identity=pending_identity,
        profile=profile,
        welcome_sent=welcome_sent,
    )
    parts = [p for p in [(ack or "").strip(), (q or "").strip()] if p]
    text = "\n\n".join(parts)
    if profile_marked_complete:
        return text
    return ensure_catalog_list_in_reply(
        has_profile=has_profile,
        profile_marked_complete=profile_marked_complete,
        profile=profile,
        steps_done=list(steps_done or []),
        assistant_content=text,
        catalog=catalog,
    )


def faq_ack_opener(
    *,
    intent: str,
    hint: str = "",
    next_question_id: str = "",
    has_profile: bool = False,
    wants_update_previous: bool = False,
    uncertain: bool = False,
) -> str:
    """Template opener used as the ack skeleton for FAQ / small-talk turns."""
    use_hint = bool(hint) and has_profile
    if wants_update_previous:
        return update_previous_ack(hint=hint if has_profile else "")
    if uncertain and (
        next_question_id in CATALOG_STEPS or next_question_id == "preferred_speaking_time"
    ):
        return uncertain_catalog_ack(hint=hint if has_profile else "")
    if use_hint:
        return hint.strip()
    if intent == "HELP":
        return (
            "I'm here to help you build your speaker profile step by step. "
            "Answer each question in your own words — you can skip optional ones."
        )
    if intent == "SMALL_TALK":
        return "Happy to chat — let's keep building your speaker profile."
    if intent == "ASK_QUESTION":
        return "Happy to clarify. Here's what I need next:"
    return "Let's get back to your speaker profile."


def faq_or_smalltalk_reply(
    *,
    intent: str,
    hint: str,
    next_question_id: str,
    catalog: Optional[Dict[str, List[str]]],
    history: Optional[List[Dict[str, Any]]] = None,
    pending_identity: Optional[Dict[str, Any]] = None,
    profile: Optional[dict] = None,
    steps_done: Optional[List[str]] = None,
    has_profile: bool = False,
    message: str = "",
    wants_update_previous: bool = False,
    uncertain: bool = False,
    welcome_sent: bool = False,
) -> str:
    opener = faq_ack_opener(
        intent=intent,
        hint=hint,
        next_question_id=next_question_id,
        has_profile=has_profile,
        wants_update_previous=wants_update_previous,
        uncertain=uncertain,
    )
    return compose_reply(
        ack=opener,
        next_question_id=next_question_id,
        catalog=catalog,
        history=history,
        pending_identity=pending_identity,
        profile=profile,
        steps_done=steps_done,
        has_profile=has_profile,
        welcome_sent=welcome_sent,
    )


def generate_assistant_reply(
    client: Any,
    *,
    user_message: str,
    ack: str,
    next_question_id: str,
    catalog: Optional[Dict[str, List[str]]] = None,
    history: Optional[List[Dict[str, Any]]] = None,
    pending_identity: Optional[Dict[str, Any]] = None,
    profile: Optional[dict] = None,
    steps_done: Optional[List[str]] = None,
    has_profile: bool = False,
    situation: str = "answered",
    facts: Optional[List[str]] = None,
    profile_marked_complete: bool = False,
    welcome_sent: bool = False,
) -> str:
    """
    Polish the compose_reply skeleton (ack + next question) with the last user message.
    Falls back to compose_reply on LLM failure. Catalog bullets are always server-appended.
    """
    already_welcome = welcome_sent or speakerpitcher_welcome_already_sent(history or [])
    fallback = compose_reply(
        ack=ack,
        next_question_id=next_question_id,
        catalog=catalog,
        history=history,
        pending_identity=pending_identity,
        profile=profile,
        steps_done=steps_done,
        has_profile=has_profile,
        profile_marked_complete=profile_marked_complete,
        welcome_sent=already_welcome,
    )
    if profile_marked_complete or client is None:
        return fallback

    next_q = build_next_question_text(
        next_question_id,
        catalog,
        history=history,
        pending_identity=pending_identity,
        profile=profile,
        welcome_sent=already_welcome,
    )
    ack_s = (ack or "").strip()
    if not ack_s and not (next_q or "").strip():
        return fallback

    fn = first_name((profile or {}).get("full_name") or "")
    if not fn and isinstance(pending_identity, dict):
        fn = first_name(str(pending_identity.get("full_name") or ""))

    options: List[str] = []
    if next_question_id in OPTION_LIST_STEPS and catalog:
        if next_question_id == "preferred_speaking_time":
            from app.services.speaker_profile_chatbot_steps import _PREFERRED_SPEAKING_TIMES

            options = list(_PREFERRED_SPEAKING_TIMES)
        else:
            options = list((catalog or {}).get(next_question_id) or [])

    facts_lines = [str(f).strip() for f in (facts or []) if str(f).strip()]
    user_ctx = build_reply_user_context(
        pending_identity=pending_identity,
        profile=profile,
        next_question_id=next_question_id or "",
    )
    context_lines = format_reply_user_context_lines(user_ctx)
    recent_inputs = recent_valid_user_inputs(history)
    skippable = next_question_id in SKIPPABLE_STEPS
    welcome_rule = (
        "FORBIDDEN: Do NOT say 'Thanks for joining SpeakerPitcher' or any joining-SpeakerPitcher welcome — "
        "it was already sent. Do not invent a welcome."
        if already_welcome
        else "Only include a SpeakerPitcher joining welcome if the template next question already has it."
    )
    name_rule = (
        "Use ONLY the provided first name in acknowledgments. "
        "Never invent a name from an email address (e.g. do not call them Alex because of alex@…)."
    )
    catalog_note = (
        "This next step uses a fixed option list. Write only a short ack + a brief ask to choose "
        "from the list — do NOT paste bullet options; the server appends them."
        if next_question_id in OPTION_LIST_STEPS
        else "Do not invent catalog options."
    )
    partial = user_ctx.get("step_partial") if isinstance(user_ctx.get("step_partial"), dict) else None
    partial_rule = (
        "step_partial is set: acknowledge values already in Known user context / recent inputs, "
        f"and ask ONLY for these missing fields: {partial.get('ask_only')}. "
        "Do not re-ask fields listed under have."
        if partial
        else "If the user already answered part of this step in recent inputs or known context, "
        "ask only for what is still missing."
    )
    refuse_skip_rule = (
        "REQUIRED: The user tried to skip a non-skippable step. In the acknowledgment you MUST clearly "
        "say this step cannot be skipped (or is required) and that they need to answer to continue. "
        "Do not offer skip, do not move to another step, and keep asking the same required question."
        if situation == "refuse_skip" or (not skippable and "required=true" in facts_lines)
        else (
            "If Skippable step is false and the user is declining/skipping, say the step cannot be skipped."
            if not skippable
            else "This step is skippable; do not invent a required-field warning."
        )
    )

    system = (
        "You polish SpeakerPitcher onboarding assistant replies.\n"
        "The server already chose the next question id and built a template skeleton.\n"
        "Rewrite into warmer, clearer copy with the SAME structure:\n"
        "1) One short acknowledgment (may lightly reference the user's last message and recent inputs)\n"
        "2) A blank line\n"
        "3) The next question for the same step/intent\n\n"
        "Rules:\n"
        "- Keep the same next step / intent as the template (do not invent a different step).\n"
        f"- {partial_rule}\n"
        f"- {refuse_skip_rule}\n"
        "- Use Known user context and Recent valid user inputs for continuity; "
        "never invent values not present there or in facts.\n"
        "- Never treat known context fields as unknown.\n"
        "- Do not invent validation outcomes, next steps, or option names.\n"
        "- Do not drop required missing asks from the template question.\n"
        "- Do not claim the profile is complete.\n"
        "- Plain text only; no markdown fences or JSON.\n"
        f"- {catalog_note}\n"
        f"- {welcome_rule}\n"
        f"- {name_rule}\n"
    )
    recent_block = (
        "\n".join(f"- {x}" for x in recent_inputs) if recent_inputs else "- (none)"
    )
    user_prompt = (
        f"Situation: {situation}\n"
        f"First name (use sparingly in ack; from profile/pending full_name only): {fn or '(unknown)'}\n"
        f"Welcome already sent: {already_welcome}\n"
        f"Next question id: {next_question_id or '(none)'}\n"
        f"Skippable step: {skippable}\n"
        f"Known user context:\n"
        + ("\n".join(f"- {x}" for x in context_lines))
        + "\n"
        f"Recent valid user inputs (oldest→newest):\n{recent_block}\n"
        f"Must-mention facts:\n"
        + ("\n".join(f"- {x}" for x in facts_lines) if facts_lines else "- (none)")
        + "\n"
        f"Allowed options (do not invent others; bullets appended by server):\n"
        + (", ".join(options) if options else "(n/a)")
        + "\n\n"
        f"Last user message:\n{(user_message or '').strip() or '(empty)'}\n\n"
        f"Template ack:\n{ack_s or '(none)'}\n\n"
        f"Template next question:\n{(next_q or '').strip() or '(none — ack only)'}\n\n"
        "Write the polished assistant reply now."
    )

    try:
        completion = client.chat.completions.create(
            model=_REPLY_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.4,
            timeout=30,
        )
        polished = (completion.choices[0].message.content or "").strip()
        if not polished:
            return fallback
        # Strip accidental fences
        if polished.startswith("```"):
            polished = polished.strip("`")
            if polished.lower().startswith("text"):
                polished = polished[4:].lstrip()
            polished = polished.strip()
        if already_welcome:
            polished = strip_duplicate_speakerpitcher_welcome(polished)
        return ensure_catalog_list_in_reply(
            has_profile=has_profile,
            profile_marked_complete=False,
            profile=profile,
            steps_done=list(steps_done or []),
            assistant_content=polished,
            catalog=catalog,
        )
    except Exception as e:
        logger.warning("generate_assistant_reply failed; using template compose_reply (%s)", e)
        return fallback


def update_previous_ack(*, hint: str = "") -> str:
    if hint and ("update" in hint.lower() or "change" in hint.lower()):
        return hint.strip()
    return (
        "Yes — you can update a previous answer. Tell me which field to change and the new value "
        "(e.g. \"change my topics to AI and Leadership\"), or answer the current question to continue."
    )


_FIELD_LABELS = {
    "full_name": "name",
    "professional_title": "title",
    "company": "company",
    "email": "email",
    "phone_number": "phone number",
    "bio": "bio",
    "linkedin_url": "LinkedIn",
    "address_city": "city",
    "address_state": "state",
    "address_country": "country",
}


def previous_fields_update_ack(updates: Dict[str, Any]) -> str:
    """Short ack when only previous-step fields were saved (current question unchanged)."""
    parts: List[str] = []
    for key, val in (updates or {}).items():
        if val is None or val == "" or val == []:
            continue
        label = _FIELD_LABELS.get(key, key.replace("_", " "))
        if isinstance(val, str):
            parts.append(f"your {label} to {val.strip()}")
        else:
            parts.append(f"your {label}")
    if not parts:
        return "Got it — I updated that."
    if len(parts) == 1:
        return f"Updated {parts[0]}."
    return "Updated " + ", ".join(parts[:-1]) + f", and {parts[-1]}."


def uncertain_catalog_ack(*, hint: str = "") -> str:
    if hint and ("list" in hint.lower() or "pick" in hint.lower() or "sure" in hint.lower()):
        return hint.strip()
    return (
        "No problem — pick any that fit from the list below "
        "(you can change them later from your profile). "
        "If none fit, say so and we can move on."
    )


_REQUIRED_FIELD_LABELS = {
    "ask_identity": "your professional name, title, and company",
    "prompt_welcome_and_contact": "your email and phone number",
    "post_welcome": "your email and phone number",
    "ready_to_create": "your email and phone number",
    "create_profile": "your email and phone number",
    "location": "your location",
    "bio": "your professional bio",
    "preferred_speaking_time": "your preferred speaking time",
    "topics": "your speaking topics",
    "speaking_formats": "your speaking formats",
    "delivery_mode": "your delivery mode",
    "target_audiences": "your target audiences",
    "talk_description": "your talk description",
    "key_takeaways": "your key takeaways",
}


def required_field_decline_ack(question_id: str = "") -> str:
    """Ack when the user declines a required (non-skippable) onboarding question."""
    label = _REQUIRED_FIELD_LABELS.get(question_id or "", "")
    if label:
        return (
            f"This step cannot be skipped — we need {label} to continue building your speaker profile. "
            "Please share an answer when you can."
        )
    return (
        "This step cannot be skipped — it is required to continue building your speaker profile. "
        "Please share an answer when you can."
    )


def refuse_skip_reply(
    *,
    next_question_id: str,
    catalog: Optional[Dict[str, List[str]]],
    history: Optional[List[Dict[str, Any]]] = None,
    pending_identity: Optional[Dict[str, Any]] = None,
    profile: Optional[dict] = None,
    steps_done: Optional[List[str]] = None,
    has_profile: bool = False,
    welcome_sent: bool = False,
) -> str:
    return compose_reply(
        ack=required_field_decline_ack(next_question_id),
        next_question_id=next_question_id,
        catalog=catalog,
        history=history,
        pending_identity=pending_identity,
        profile=profile,
        steps_done=steps_done,
        has_profile=has_profile,
        welcome_sent=welcome_sent,
    )


def conflict_confirm_reply(pending: Dict[str, Any]) -> str:
    field_name = pending.get("field") or "that field"
    old = pending.get("oldValue")
    new = pending.get("newValue")
    return (
        f"I currently have {field_name} as {old!r}. "
        f"Do you want me to change it to {new!r}? (yes/no)"
    )


def quit_reply() -> str:
    return (
        "No problem — we can pause here. "
        "Come back anytime and we'll pick up your speaker profile where you left off."
    )


def gibberish_reply(next_q: str) -> str:
    if next_q:
        return f"I'm sorry, I couldn't understand that.\n\n{next_q}"
    return "I'm sorry, I couldn't understand that. Could you answer the question again?"


def off_list_ack(rejected: List[str], accepted: Optional[List[str]] = None) -> str:
    rejected_label = ", ".join(str(x) for x in (rejected or []) if str(x).strip())
    if accepted:
        saved = ", ".join(accepted)
        if rejected_label:
            return (
                f"I've saved {saved}. "
                f"{rejected_label} isn't on this list, but you can add "
                f"{'it' if len(rejected) == 1 else 'those'} later from your speaker profile."
            )
        return (
            f"I've saved {saved}. "
            "The other choices aren't on this list, but you can add them later from your speaker profile."
        )
    if rejected_label:
        return (
            f"{rejected_label} isn't on this list, but you can add "
            f"{'it' if len(rejected or []) == 1 else 'those'} later from your speaker profile."
        )
    return "That choice isn't on this list, but you can add it later from your speaker profile."


def off_list_reask_ack(rejected: List[str], *, field_label: str = "option") -> str:
    """Stay on the current step: name the rejected value, ask to pick from list, mention profile later."""
    rejected_label = ", ".join(str(x) for x in (rejected or []) if str(x).strip())
    if rejected_label:
        return (
            f"{rejected_label} isn't on the available {field_label} list. "
            "Please choose one or more from the options below. "
            "You can add other values later from your speaker profile."
        )
    return (
        f"That choice isn't on the available {field_label} list. "
        "Please choose one or more from the options below. "
        "You can add other values later from your speaker profile."
    )
