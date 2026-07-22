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
    build_identity_welcome_reply,
    build_step_user_message,
    ensure_catalog_list_in_reply,
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


def completion_message() -> str:
    return _PROFILE_COMPLETION_MESSAGE


def first_name(full_name: str) -> str:
    raw = (full_name or "").strip()
    if not raw:
        return ""
    before = raw.split(",")[0].strip()
    parts = before.split()
    return parts[0] if parts else ""


def build_next_question_text(
    question_id: str,
    catalog: Optional[Dict[str, List[str]]],
    *,
    history: Optional[List[Dict[str, Any]]] = None,
    pending_identity: Optional[Dict[str, Any]] = None,
    welcome_sent: bool = False,
) -> str:
    if not question_id:
        return ""
    if question_id == PRE_CREATE_ASK_IDENTITY:
        return "Please share your professional name, title, and company."
    if question_id == PRE_CREATE_PROMPT_WELCOME:
        full_name = (pending_identity or {}).get("full_name") or ""
        already = welcome_sent or speakerpitcher_welcome_already_sent(history or [])
        if full_name and not already:
            return build_identity_welcome_reply(full_name)
        return _IDENTITY_EMAIL_PHONE_QUESTION
    if question_id in (PRE_CREATE_POST_WELCOME, PRE_CREATE_READY, "create_profile"):
        return _IDENTITY_EMAIL_PHONE_QUESTION
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

    system = (
        "You polish SpeakerPitcher onboarding assistant replies.\n"
        "The server already chose the next question and built a template skeleton.\n"
        "Rewrite into warmer, clearer copy with the SAME structure:\n"
        "1) One short acknowledgment (may lightly reference the user's last message)\n"
        "2) A blank line\n"
        "3) The next question — same meaning as the template (do not change what is asked)\n\n"
        "Rules:\n"
        "- Stay close to the skeleton; improve wording only.\n"
        "- Do not invent validation outcomes, next steps, or option names.\n"
        "- Do not drop required asks from the template question.\n"
        "- Do not claim the profile is complete.\n"
        "- Plain text only; no markdown fences or JSON.\n"
        f"- {catalog_note}\n"
        f"- {welcome_rule}\n"
        f"- {name_rule}\n"
    )
    user_prompt = (
        f"Situation: {situation}\n"
        f"First name (use sparingly in ack; from profile/pending full_name only): {fn or '(unknown)'}\n"
        f"Welcome already sent: {already_welcome}\n"
        f"Next question id: {next_question_id or '(none)'}\n"
        f"Skippable step: {skippable}\n"
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
            f"This is a required field — we need {label} to continue building your speaker profile. "
            "Please share an answer when you can."
        )
    return (
        "This is a required field — we need it to continue building your speaker profile. "
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
