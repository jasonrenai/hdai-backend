"""
Speaker onboarding agent — backend-owned conversational orchestrator.

Pipeline per turn:
  ModerateInput → GetOnboardingState → AnalyzeUserMessage →
  Validate / FAQ / Skip / Quit → UpdateSpeakerProfile → SelectNextQuestion → Respond
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from openai import OpenAI

from app.services.onboarding_agent.analyze import (
    analyze_user_message,
    rescue_intent,
    should_validate_as_answer,
)
from app.services.onboarding_agent.moderate import moderate_input, refuse_prompt_injection
from app.services.onboarding_agent.profile_update import (
    merge_pending_identity,
    plan_profile_update,
)
from app.services.onboarding_agent.question_schema import get_question
from app.services.onboarding_agent.respond import (
    completion_message,
    conflict_confirm_reply,
    faq_ack_opener,
    first_name,
    generate_assistant_reply,
    off_list_ack,
    off_list_reask_ack,
    previous_fields_update_ack,
    quit_reply,
    required_field_decline_ack,
    build_next_question_text,
)
from app.services.onboarding_agent.state import (
    derive_pre_create_question,
    get_onboarding_state,
    select_next_question,
)
from app.services.onboarding_agent.validate import validate_answer
from app.services.speaker_profile_chatbot_steps import (
    PRE_CREATE_ASK_IDENTITY,
    PRE_CREATE_PROMPT_WELCOME,
    SKIPPABLE_STEPS,
    detect_continue_intent,
    detect_skip_intent,
    extract_pre_create_identity,
    last_assistant_asked_testimonial,
    merge_steps_done,
    speakerpitcher_welcome_already_sent,
    speakerpitcher_welcome_in_text,
    steps_from_saved_fields,
)

logger = logging.getLogger(__name__)


class SpeakerOnboardingAgent:
    """One-turn orchestrator; persistence delegated to SpeakerProfileChatbotService."""

    def __init__(self, chatbot_service: Any):
        self.svc = chatbot_service

    def _reply(
        self,
        client: Any,
        *,
        user_message: str,
        ack: str,
        next_question_id: str,
        catalog: Optional[Dict[str, List[str]]],
        history: Optional[List[Dict[str, Any]]] = None,
        pending_identity: Optional[Dict[str, Any]] = None,
        profile: Optional[dict] = None,
        steps_done: Optional[List[str]] = None,
        has_profile: bool = False,
        situation: str = "answered",
        facts: Optional[List[str]] = None,
        welcome_sent: Optional[bool] = None,
    ) -> str:
        """Polish compose_reply skeleton via LLM; templates used as fallback."""
        sent = (
            bool(welcome_sent)
            if welcome_sent is not None
            else bool(getattr(self, "_turn_welcome_sent", False))
        )
        return generate_assistant_reply(
            client,
            user_message=user_message or "",
            ack=ack,
            next_question_id=next_question_id,
            catalog=catalog,
            history=history,
            pending_identity=pending_identity,
            profile=profile,
            steps_done=steps_done,
            has_profile=has_profile,
            situation=situation,
            facts=facts,
            welcome_sent=sent,
        )

    async def handle_turn(
        self,
        *,
        message: str,
        chat_session_id: Optional[str] = None,
        jwt_user: Optional[Dict[str, Any]] = None,
        speaker_profile_id: Optional[str] = None,
    ) -> dict:
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
        catalog = await self.svc._load_catalog_name_lists()
        self.svc._catalog_name_lists = catalog

        session = None
        profile = None
        history: List[Dict[str, Any]] = []
        requested_profile_id = (speaker_profile_id or "").strip() or None
        speaker_profile_id = None

        if chat_session_id:
            session = await self.svc.chat_session_model.get_by_id(chat_session_id)
            if session:
                speaker_profile_id = (session.get("speaker_profile_id") or "").strip() or None
                conv = session.get("conversation") or []
                history = [
                    {"role": m.get("role", "user"), "content": m.get("content", "")}
                    for m in conv
                ]

        if not speaker_profile_id and requested_profile_id:
            speaker_profile_id = requested_profile_id

        # One-time welcome: session flag OR history detect (legacy sessions)
        self._turn_welcome_sent = bool((session or {}).get("speakerpitcher_welcome_sent")) or (
            speakerpitcher_welcome_already_sent(history)
        )
        if speaker_profile_id:
            profile = await self.svc.profile_model.get_profile(speaker_profile_id)
            if profile:
                profile["_id"] = str(profile["_id"])
            else:
                speaker_profile_id = None

        if session and chat_session_id and speaker_profile_id:
            existing_spid = (session.get("speaker_profile_id") or "").strip()
            if existing_spid != speaker_profile_id:
                await self.svc.chat_session_model.update_speaker_profile_id(
                    chat_session_id, speaker_profile_id
                )
                session["speaker_profile_id"] = speaker_profile_id

        steps_done: List[str] = list((session or {}).get("onboarding_steps_done") or [])
        skipped: List[str] = list((session or {}).get("skipped_questions") or [])
        pending_identity = dict((session or {}).get("pending_identity") or {})
        pending_confirmation = (session or {}).get("pending_confirmation")

        state = get_onboarding_state(
            profile=profile,
            session={
                **(session or {}),
                "onboarding_steps_done": steps_done,
                "skipped_questions": skipped,
                "pending_identity": pending_identity or None,
                "pending_confirmation": pending_confirmation,
            },
            history=history,
            message=message or "",
            catalog=catalog,
        )

        ask_identity = (not state.has_profile) and (
            state.current_question_id == PRE_CREATE_ASK_IDENTITY
            or not (pending_identity.get("full_name") or "").strip()
        )
        moderation = moderate_input(message or "", ask_identity=ask_identity)
        if moderation.action in ("REFUSE", "WARN", "REDIRECT"):
            q_text = build_next_question_text(
                state.current_question_id,
                catalog,
                history=history,
                pending_identity=pending_identity,
                welcome_sent=self._turn_welcome_sent,
            )
            assistant = moderation.message
            if q_text and moderation.action in ("REFUSE", "WARN", "REDIRECT"):
                # Always restate the real next question after moderation.
                if q_text not in assistant:
                    assistant = f"{moderation.message}\n\n{q_text}"
            return await self._persist_and_return(
                message=message,
                assistant=assistant,
                chat_session_id=chat_session_id,
                session=session,
                speaker_profile_id=speaker_profile_id,
                profile=profile,
                action=None,
                pending_identity=pending_identity or None,
            )

        # Pending confirmation is resolved after Analyze (continueIntent / skipIntent flags).
        question = state.current_question or get_question(state.current_question_id, catalog)
        analysis = analyze_user_message(
            client,
            message=message or "",
            history=history,
            state=state,
            question=question,
        )
        analysis = rescue_intent(analysis, message or "")

        # LLM caught a jailbreak the cheap regex pre-check missed
        if analysis.prompt_injection:
            moderation = refuse_prompt_injection(ask_identity=ask_identity)
            q_text = build_next_question_text(
                state.current_question_id,
                catalog,
                history=history,
                pending_identity=pending_identity,
                welcome_sent=self._turn_welcome_sent,
            )
            assistant = moderation.message
            if q_text and q_text not in assistant:
                assistant = f"{moderation.message}\n\n{q_text}"
            return await self._persist_and_return(
                message=message,
                assistant=assistant,
                chat_session_id=chat_session_id,
                session=session,
                speaker_profile_id=speaker_profile_id,
                profile=profile,
                action=None,
                pending_identity=pending_identity or None,
            )

        user_confirms = analysis.continue_intent or detect_continue_intent(message or "")
        user_declines = analysis.skip_intent or detect_skip_intent(message or "")

        if pending_confirmation and user_confirms:
            plan = plan_profile_update(
                profile=profile,
                updates={},
                intent="CHANGE_PREVIOUS",
                pending_confirmation=pending_confirmation,
                user_confirmed=True,
            )
            action = None
            if plan.updates and state.has_profile and speaker_profile_id:
                result = await self.svc._execute_upsert(plan.updates, speaker_profile_id, jwt_user)
                action = result.get("action")
                if result.get("profile"):
                    profile = result["profile"]
                    profile["_id"] = str(profile.get("_id") or speaker_profile_id)
                steps_done = merge_steps_done(
                    steps_done, steps_from_saved_fields(result.get("saved_fields") or [])
                )
            pending_confirmation = None
            chat_session_id = await self._ensure_session_meta(
                chat_session_id=chat_session_id,
                session=session,
                speaker_profile_id=speaker_profile_id,
                steps_done=steps_done,
                skipped=skipped,
                pending_identity=pending_identity,
                pending_confirmation=None,
            )
            session = await self.svc.chat_session_model.get_by_id(chat_session_id) if chat_session_id else session
            next_id = select_next_question(
                profile=profile,
                steps_done=steps_done,
                has_profile=bool(profile),
                catalog=catalog,
                history=history,
                message="",
            )
            assistant = self._reply(
                client,
                user_message=message or "",
                ack="Got it — updated.",
                next_question_id=next_id,
                catalog=catalog,
                history=history,
                pending_identity=pending_identity,
                profile=profile,
                steps_done=steps_done,
                has_profile=bool(profile),
                situation="confirm_applied",
            )
            return await self._persist_and_return(
                message=message,
                assistant=assistant,
                chat_session_id=chat_session_id,
                session=session,
                speaker_profile_id=speaker_profile_id,
                profile=profile,
                action=action,
                steps_done=steps_done,
                skipped=skipped,
                pending_identity=pending_identity,
                pending_confirmation=None,
            )

        if pending_confirmation and user_declines and not analysis.has_extractable_content():
            chat_session_id = await self._ensure_session_meta(
                chat_session_id=chat_session_id,
                session=session,
                speaker_profile_id=speaker_profile_id,
                steps_done=steps_done,
                skipped=skipped,
                pending_identity=pending_identity,
                pending_confirmation=None,
            )
            next_id = select_next_question(
                profile=profile,
                steps_done=steps_done,
                has_profile=bool(profile),
                catalog=catalog,
                history=history,
                message="",
            )
            assistant = self._reply(
                client,
                user_message=message or "",
                ack="Okay, I'll keep it as is.",
                next_question_id=next_id,
                catalog=catalog,
                history=history,
                pending_identity=pending_identity,
                profile=profile,
                steps_done=steps_done,
                has_profile=bool(profile),
                situation="confirm_declined",
            )
            return await self._persist_and_return(
                message=message,
                assistant=assistant,
                chat_session_id=chat_session_id,
                session=session,
                speaker_profile_id=speaker_profile_id,
                profile=profile,
                action=None,
                steps_done=steps_done,
                skipped=skipped,
                pending_identity=pending_identity,
                pending_confirmation=None,
            )

        # Pre-create identity: prefer dedicated extractor when on ask_identity
        if (
            not state.has_profile
            and state.current_question_id == PRE_CREATE_ASK_IDENTITY
            and analysis.intent in ("ANSWER", "UNKNOWN", "CHANGE_PREVIOUS")
            and not analysis.greeting_only
        ):
            extracted = extract_pre_create_identity(client, message or "")
            if extracted:
                analysis.profile_updates = {**analysis.profile_updates, **extracted}
                analysis.intent = "ANSWER"
                analysis.confidence = max(analysis.confidence, 0.85)
                analysis.gibberish = False
                analysis.greeting_only = False

        action: Optional[str] = None
        profile_marked_complete = False

        # Intent branches that do not advance
        if analysis.intent == "QUIT":
            chat_session_id = await self._ensure_session_meta(
                chat_session_id=chat_session_id,
                session=session,
                speaker_profile_id=speaker_profile_id,
                steps_done=steps_done,
                skipped=skipped,
                pending_identity=pending_identity,
                pending_confirmation=pending_confirmation,
                conversation_status="QUIT",
            )
            return await self._persist_and_return(
                message=message,
                assistant=quit_reply(),
                chat_session_id=chat_session_id,
                session=session,
                speaker_profile_id=speaker_profile_id,
                profile=profile,
                action=None,
                conversation_status="QUIT",
            )

        conversational = (
            analysis.intent in ("ASK_QUESTION", "HELP", "SMALL_TALK", "UNKNOWN")
            or analysis.wants_update_previous
            or analysis.uncertain
            or analysis.meta_question
            or analysis.greeting_only
            or (
                analysis.off_topic
                and analysis.intent not in ("ANSWER", "SKIP", "CHANGE_PREVIOUS")
            )
        )
        if should_validate_as_answer(
            analysis, message or "", state.current_question_id, pending_identity
        ):
            conversational = False

        if conversational and analysis.intent not in ("ANSWER", "CHANGE_PREVIOUS", "SKIP"):
            next_id = state.current_question_id
            if not state.has_profile:
                next_id = derive_pre_create_question(pending_identity)
            intent = analysis.intent if not analysis.off_topic else "UNKNOWN"
            situation = (
                "uncertain"
                if analysis.uncertain
                else "update_previous"
                if analysis.wants_update_previous
                else "faq"
            )
            assistant = self._reply(
                client,
                user_message=message or "",
                ack=faq_ack_opener(
                    intent=intent,
                    hint=analysis.assistant_hint,
                    next_question_id=next_id,
                    has_profile=state.has_profile,
                    wants_update_previous=analysis.wants_update_previous,
                    uncertain=analysis.uncertain,
                ),
                next_question_id=next_id,
                catalog=catalog,
                history=history,
                pending_identity=pending_identity,
                profile=profile,
                steps_done=steps_done,
                has_profile=state.has_profile,
                situation=situation,
            )
            return await self._persist_and_return(
                message=message,
                assistant=assistant,
                chat_session_id=chat_session_id,
                session=session,
                speaker_profile_id=speaker_profile_id,
                profile=profile,
                action=None,
                steps_done=steps_done,
                skipped=skipped,
                pending_identity=pending_identity,
                pending_confirmation=pending_confirmation,
            )

        if analysis.intent in ("ANSWER", "CHANGE_PREVIOUS", "UNKNOWN") and not should_validate_as_answer(
            analysis, message or "", state.current_question_id, pending_identity
        ):
            next_id = state.current_question_id
            if not state.has_profile:
                next_id = derive_pre_create_question(pending_identity)
            intent = (
                "HELP"
                if analysis.uncertain or analysis.intent in ("ANSWER", "UNKNOWN")
                else "ASK_QUESTION"
            )
            assistant = self._reply(
                client,
                user_message=message or "",
                ack=faq_ack_opener(
                    intent=intent,
                    hint=analysis.assistant_hint,
                    next_question_id=next_id,
                    has_profile=state.has_profile,
                    wants_update_previous=analysis.wants_update_previous,
                    uncertain=analysis.uncertain,
                ),
                next_question_id=next_id,
                catalog=catalog,
                history=history,
                pending_identity=pending_identity,
                profile=profile,
                steps_done=steps_done,
                has_profile=state.has_profile,
                situation="uncertain" if analysis.uncertain else "faq",
            )
            return await self._persist_and_return(
                message=message,
                assistant=assistant,
                chat_session_id=chat_session_id,
                session=session,
                speaker_profile_id=speaker_profile_id,
                profile=profile,
                action=None,
                steps_done=steps_done,
                skipped=skipped,
                pending_identity=pending_identity,
                pending_confirmation=pending_confirmation,
            )

        # Skip — never when the message also carries extractable profile content
        # (e.g. "no my company is DCL" while on Memberships).
        can_skip = not analysis.has_extractable_content() and (
            analysis.intent == "SKIP"
            or analysis.skip_intent
            or (
                state.has_profile
                and state.current_question_id in SKIPPABLE_STEPS
                and detect_skip_intent(message or "")
            )
        )
        if can_skip:
            step = state.current_question_id
            # Required (non-skippable) — including pre-create identity/contact
            if step not in SKIPPABLE_STEPS:
                next_id = step
                if not state.has_profile:
                    next_id = derive_pre_create_question(pending_identity) or step
                assistant = self._reply(
                    client,
                    user_message=message or "",
                    ack=required_field_decline_ack(next_id),
                    next_question_id=next_id,
                    catalog=catalog,
                    history=history,
                    pending_identity=pending_identity,
                    profile=profile,
                    steps_done=steps_done,
                    has_profile=state.has_profile,
                    situation="refuse_skip",
                    facts=["required=true", f"field={next_id}"],
                )
                return await self._persist_and_return(
                    message=message,
                    assistant=assistant,
                    chat_session_id=chat_session_id,
                    session=session,
                    speaker_profile_id=speaker_profile_id,
                    profile=profile,
                    action=None,
                    steps_done=steps_done,
                    skipped=skipped,
                    pending_identity=pending_identity,
                )
            if state.has_profile:
                steps_done = merge_steps_done(steps_done, [step])
                if step not in skipped:
                    skipped = list(skipped) + [step]
                # Completing via skip on testimonial
                if step == "testimonial" or last_assistant_asked_testimonial(history):
                    if speaker_profile_id and profile:
                        profile_marked_complete, profile, steps_done = (
                            await self.svc._try_auto_mark_profile_complete(
                                speaker_profile_id, profile, steps_done, jwt_user=jwt_user
                            )
                        )
                        if profile_marked_complete:
                            action = "completed"
                            assistant = completion_message()
                            chat_session_id = await self._ensure_session_meta(
                                chat_session_id=chat_session_id,
                                session=session,
                                speaker_profile_id=speaker_profile_id,
                                steps_done=steps_done,
                                skipped=skipped,
                                pending_identity=pending_identity,
                                pending_confirmation=None,
                                conversation_status="COMPLETED",
                            )
                            return await self._persist_and_return(
                                message=message,
                                assistant=assistant,
                                chat_session_id=chat_session_id,
                                session=session,
                                speaker_profile_id=speaker_profile_id,
                                profile=profile,
                                action=action,
                                steps_done=steps_done,
                                skipped=skipped,
                                conversation_status="COMPLETED",
                            )
                next_id = select_next_question(
                    profile=profile,
                    steps_done=steps_done,
                    has_profile=True,
                    catalog=catalog,
                    history=history,
                    message="",
                )
                fn = first_name((profile or {}).get("full_name") or "")
                ack = f"No problem{', ' + fn if fn else ''} — we can skip that."
                assistant = self._reply(
                    client,
                    user_message=message or "",
                    ack=ack,
                    next_question_id=next_id,
                    catalog=catalog,
                    history=history,
                    pending_identity=pending_identity,
                    profile=profile,
                    steps_done=steps_done,
                    has_profile=True,
                    situation="skip",
                )
                return await self._persist_and_return(
                    message=message,
                    assistant=assistant,
                    chat_session_id=chat_session_id,
                    session=session,
                    speaker_profile_id=speaker_profile_id,
                    profile=profile,
                    action=None,
                    steps_done=steps_done,
                    skipped=skipped,
                    pending_identity=pending_identity,
                )

        # ANSWER / CHANGE_PREVIOUS / UNKNOWN treated as potential answer
        validation = validate_answer(
            question=question,
            analysis=analysis,
            message=message or "",
            catalog=catalog,
            client=client,
            current_step=state.current_question_id,
        )

        if analysis.gibberish and not validation.valid:
            return await self._persist_and_return(
                message=message,
                assistant=self._reply(
                    client,
                    user_message=message or "",
                    ack="I'm sorry, I couldn't understand that.",
                    next_question_id=state.current_question_id,
                    catalog=catalog,
                    history=history,
                    pending_identity=pending_identity,
                    profile=profile,
                    steps_done=steps_done,
                    has_profile=state.has_profile,
                    situation="gibberish",
                ),
                chat_session_id=chat_session_id,
                session=session,
                speaker_profile_id=speaker_profile_id,
                profile=profile,
                action=None,
                steps_done=steps_done,
                skipped=skipped,
                pending_identity=pending_identity,
            )

        if not validation.valid:
            # Persist any partial pre-create extractions even when validation fails
            # (e.g. INVALID_PHONE still keeps email in pending).
            if not state.has_profile and validation.normalized_updates:
                pending_identity = merge_pending_identity(
                    pending_identity, validation.normalized_updates
                )

            # Invalid phone: keep email, ask phone only — never re-welcome / re-ask email.
            if (
                not state.has_profile
                and validation.reason == "INVALID_PHONE"
            ):
                pending_email = (pending_identity.get("email") or "").strip()
                msg = (
                    validation.message
                    or (
                        "That phone number doesn't look valid. "
                        "Please share a valid phone number (with country code if possible)."
                    )
                )
                if pending_email:
                    ack = (
                        f"{msg} I've still got your email ({pending_email}). "
                        "Could you share a valid phone number?"
                    )
                else:
                    ack = msg
                chat_session_id = await self._ensure_session_meta(
                    chat_session_id=chat_session_id,
                    session=session,
                    speaker_profile_id="",
                    steps_done=steps_done,
                    skipped=skipped,
                    pending_identity=pending_identity,
                )
                assistant = self._reply(
                    client,
                    user_message=message or "",
                    ack=ack,
                    next_question_id="",
                    catalog=catalog,
                    history=history,
                    pending_identity=pending_identity,
                    profile=None,
                    steps_done=steps_done,
                    has_profile=False,
                    situation="invalid_phone",
                    facts=[f"email={pending_email}"] if pending_email else None,
                )
                return await self._persist_and_return(
                    message=message,
                    assistant=assistant,
                    chat_session_id=chat_session_id,
                    session=session,
                    speaker_profile_id=speaker_profile_id,
                    profile=profile,
                    action=None,
                    steps_done=steps_done,
                    skipped=skipped,
                    pending_identity=pending_identity,
                )

            if not state.has_profile and validation.normalized_updates:
                next_id = derive_pre_create_question(pending_identity)
                msg = validation.message or "Could you try answering that again?"
                pending_email = (pending_identity.get("email") or "").strip()
                pending_phone = (pending_identity.get("phone_number") or "").strip()
                if not (pending_identity.get("full_name") or "").strip():
                    msg = (
                        "Please share your professional name, title, and company first "
                        "(e.g., Jane Doe, MBA, PMP — Speaker, Acme Corp)."
                    )
                    next_id = ""
                elif pending_email and not pending_phone:
                    # Never re-ask email once saved
                    msg = validation.message or (
                        f"Thanks — I've got your email ({pending_email}). "
                        "Could you also share your phone number?"
                    )
                    next_id = ""
                assistant = self._reply(
                    client,
                    user_message=message or "",
                    ack=msg,
                    next_question_id=next_id,
                    catalog=catalog,
                    history=history,
                    pending_identity=pending_identity,
                    profile=profile,
                    steps_done=steps_done,
                    has_profile=False,
                    situation="reask",
                    facts=[f"email={pending_email}"] if pending_email else None,
                )
                return await self._persist_and_return(
                    message=message,
                    assistant=assistant,
                    chat_session_id=chat_session_id,
                    session=session,
                    speaker_profile_id=speaker_profile_id,
                    profile=profile,
                    action=None,
                    steps_done=steps_done,
                    skipped=skipped,
                    pending_identity=pending_identity,
                )
            msg = validation.message or "Could you try answering that again?"
            # Pre-create contact re-ask: if we already have email, ask phone only
            if not state.has_profile and (pending_identity.get("email") or "").strip():
                pending_email = (pending_identity.get("email") or "").strip()
                if not (pending_identity.get("phone_number") or "").strip():
                    ack = (
                        f"{msg} I've still got your email ({pending_email}). "
                        "Could you share your phone number?"
                    )
                    assistant = self._reply(
                        client,
                        user_message=message or "",
                        ack=ack,
                        next_question_id="",
                        catalog=catalog,
                        history=history,
                        pending_identity=pending_identity,
                        profile=None,
                        steps_done=steps_done,
                        has_profile=False,
                        situation="reask",
                        facts=[f"email={pending_email}"],
                    )
                    return await self._persist_and_return(
                        message=message,
                        assistant=assistant,
                        chat_session_id=chat_session_id,
                        session=session,
                        speaker_profile_id=speaker_profile_id,
                        profile=profile,
                        action=None,
                        steps_done=steps_done,
                        skipped=skipped,
                        pending_identity=pending_identity,
                    )
            if validation.reason in ("UNCERTAIN", "META", "OFF_LIST"):
                if validation.reason == "OFF_LIST":
                    ack = validation.message or off_list_reask_ack(
                        validation.rejected_options,
                        field_label="preferred speaking time"
                        if state.current_question_id == "preferred_speaking_time"
                        else "option",
                    )
                    assistant = self._reply(
                        client,
                        user_message=message or "",
                        ack=ack,
                        next_question_id=state.current_question_id,
                        catalog=catalog,
                        history=history,
                        pending_identity=pending_identity,
                        profile=profile,
                        steps_done=steps_done,
                        has_profile=state.has_profile,
                        situation="off_list",
                        facts=list(validation.rejected_options or []),
                    )
                else:
                    assistant = self._reply(
                        client,
                        user_message=message or "",
                        ack=faq_ack_opener(
                            intent="HELP" if validation.reason == "UNCERTAIN" else "ASK_QUESTION",
                            hint=msg,
                            next_question_id=state.current_question_id,
                            has_profile=state.has_profile,
                            uncertain=validation.reason == "UNCERTAIN",
                        ),
                        next_question_id=state.current_question_id,
                        catalog=catalog,
                        history=history,
                        pending_identity=pending_identity,
                        profile=profile,
                        steps_done=steps_done,
                        has_profile=state.has_profile,
                        situation="uncertain" if validation.reason == "UNCERTAIN" else "faq",
                    )
            else:
                assistant = self._reply(
                    client,
                    user_message=message or "",
                    ack=msg,
                    next_question_id=state.current_question_id if state.has_profile else "",
                    catalog=catalog,
                    history=history,
                    pending_identity=pending_identity,
                    profile=profile,
                    steps_done=steps_done,
                    has_profile=state.has_profile,
                    situation="reask",
                )
            return await self._persist_and_return(
                message=message,
                assistant=assistant,
                chat_session_id=chat_session_id,
                session=session,
                speaker_profile_id=speaker_profile_id,
                profile=profile,
                action=None,
                steps_done=steps_done,
                skipped=skipped,
                pending_identity=pending_identity,
            )

        updates = dict(validation.normalized_updates or {})

        # Off-list catalog: mark done, no upsert
        if validation.mark_step_done_off_list and state.has_profile:
            steps_done = merge_steps_done(steps_done, [state.current_question_id])
            next_id = select_next_question(
                profile=profile,
                steps_done=steps_done,
                has_profile=True,
                catalog=catalog,
                history=history,
                message="",
            )
            ack = off_list_ack(validation.rejected_options, validation.accepted_options)
            assistant = self._reply(
                client,
                user_message=message or "",
                ack=ack,
                next_question_id=next_id,
                catalog=catalog,
                history=history,
                pending_identity=pending_identity,
                profile=profile,
                steps_done=steps_done,
                has_profile=True,
                situation="off_list",
                facts=list(validation.rejected_options or []),
            )
            return await self._persist_and_return(
                message=message,
                assistant=assistant,
                chat_session_id=chat_session_id,
                session=session,
                speaker_profile_id=speaker_profile_id,
                profile=profile,
                action=None,
                steps_done=steps_done,
                skipped=skipped,
                pending_identity=pending_identity,
            )

        # Pre-create: accumulate identity / contact; create when ready
        if not state.has_profile:
            pending_identity = merge_pending_identity(pending_identity, updates)
            # Also merge analysis extras
            pending_identity = merge_pending_identity(pending_identity, analysis.profile_updates)

            email = (pending_identity.get("email") or updates.get("email") or "").strip().lower()
            phone = (pending_identity.get("phone_number") or updates.get("phone_number") or "").strip()
            full_name = (pending_identity.get("full_name") or "").strip()
            if email:
                pending_identity["email"] = email
            if phone:
                pending_identity["phone_number"] = phone

            # After identity → welcome + ask contact
            if full_name and not email and not phone and state.current_question_id == PRE_CREATE_ASK_IDENTITY:
                chat_session_id = await self._ensure_session_meta(
                    chat_session_id=chat_session_id,
                    session=session,
                    speaker_profile_id="",
                    steps_done=steps_done,
                    skipped=skipped,
                    pending_identity=pending_identity,
                    pending_confirmation=None,
                )
                assistant = self._reply(
                    client,
                    user_message=message or "",
                    ack="",
                    next_question_id=PRE_CREATE_PROMPT_WELCOME,
                    catalog=catalog,
                    history=history,
                    pending_identity=pending_identity,
                    profile=None,
                    steps_done=steps_done,
                    has_profile=False,
                    situation="welcome",
                )
                return await self._persist_and_return(
                    message=message,
                    assistant=assistant,
                    chat_session_id=chat_session_id,
                    session=session,
                    speaker_profile_id=None,
                    profile=None,
                    action=None,
                    steps_done=steps_done,
                    skipped=skipped,
                    pending_identity=pending_identity,
                )

            if email and phone and full_name:
                create_args = {
                    "full_name": full_name,
                    "professional_title": (pending_identity.get("professional_title") or "").strip(),
                    "company": (pending_identity.get("company") or "").strip(),
                    "email": email,
                    "phone_number": phone,
                }
                result = await self.svc._execute_upsert(create_args, None, jwt_user)
                if result.get("action") == "created" and result.get("profile"):
                    profile = result["profile"]
                    speaker_profile_id = str(profile.get("_id"))
                    profile["_id"] = speaker_profile_id
                    action = "created"
                    pending_identity = {}
                    next_id = select_next_question(
                        profile=profile,
                        steps_done=steps_done,
                        has_profile=True,
                        catalog=catalog,
                        history=history,
                        message="",
                        pending_identity=None,
                    )
                    fn = first_name(full_name)
                    ack = f"Perfect{', ' + fn if fn else ''} — your speaker profile is started."
                    assistant = self._reply(
                        client,
                        user_message=message or "",
                        ack=ack,
                        next_question_id=next_id,
                        catalog=catalog,
                        history=history,
                        pending_identity=None,
                        profile=profile,
                        steps_done=steps_done,
                        has_profile=True,
                        situation="created",
                    )
                    return await self._persist_and_return(
                        message=message,
                        assistant=assistant,
                        chat_session_id=chat_session_id,
                        session=session,
                        speaker_profile_id=speaker_profile_id,
                        profile=profile,
                        action=action,
                        steps_done=steps_done,
                        skipped=skipped,
                        pending_identity={},
                    )
                if result.get("action") == "email_required":
                    assistant = self._reply(
                        client,
                        user_message=message or "",
                        ack="Please share a valid email address.",
                        next_question_id="",
                        catalog=catalog,
                        history=history,
                        pending_identity=pending_identity,
                        has_profile=False,
                        situation="reask",
                    )
                elif result.get("action") == "phone_required":
                    assistant = self._reply(
                        client,
                        user_message=message or "",
                        ack="Thanks — could you also share your phone number?",
                        next_question_id="",
                        catalog=catalog,
                        history=history,
                        pending_identity=pending_identity,
                        has_profile=False,
                        situation="reask",
                    )
                else:
                    assistant = self._reply(
                        client,
                        user_message=message or "",
                        ack="I still need your name, email, and phone to create your profile.",
                        next_question_id="",
                        catalog=catalog,
                        history=history,
                        pending_identity=pending_identity,
                        has_profile=False,
                        situation="reask",
                    )
                chat_session_id = await self._ensure_session_meta(
                    chat_session_id=chat_session_id,
                    session=session,
                    speaker_profile_id="",
                    steps_done=steps_done,
                    skipped=skipped,
                    pending_identity=pending_identity,
                )
                return await self._persist_and_return(
                    message=message,
                    assistant=assistant,
                    chat_session_id=chat_session_id,
                    session=session,
                    speaker_profile_id=None,
                    profile=None,
                    action=None,
                    pending_identity=pending_identity,
                )

            # Still collecting pre-create fields — ask for whatever is actually missing
            chat_session_id = await self._ensure_session_meta(
                chat_session_id=chat_session_id,
                session=session,
                speaker_profile_id="",
                steps_done=steps_done,
                skipped=skipped,
                pending_identity=pending_identity,
            )
            if not full_name:
                ack = (
                    "Let's start with your professional name, title, and company "
                    "(e.g., Jane Doe, MBA, PMP — Speaker, Acme Corp)."
                )
                facts = None
            elif email and not phone:
                ack = (
                    f"Thanks — I've got your email ({email}). "
                    "Could you also share your phone number?"
                )
                facts = [f"email={email}"]
            elif phone and not email:
                ack = "Thanks — could you share your email address?"
                facts = None
            else:
                ack = "Could you please provide your email and phone number?"
                facts = None
            assistant = self._reply(
                client,
                user_message=message or "",
                ack=ack,
                next_question_id="",
                catalog=catalog,
                history=history,
                pending_identity=pending_identity,
                has_profile=False,
                situation="reask",
                facts=facts,
            )
            return await self._persist_and_return(
                message=message,
                assistant=assistant,
                chat_session_id=chat_session_id,
                session=session,
                speaker_profile_id=None,
                profile=None,
                action=None,
                pending_identity=pending_identity,
            )

        # Post-create update
        plan = plan_profile_update(
            profile=profile,
            updates=updates,
            intent=analysis.intent,
            pending_confirmation=None,
            user_confirmed=False,
        )
        if plan.needs_confirmation and plan.pending_confirmation:
            chat_session_id = await self._ensure_session_meta(
                chat_session_id=chat_session_id,
                session=session,
                speaker_profile_id=speaker_profile_id,
                steps_done=steps_done,
                skipped=skipped,
                pending_identity=pending_identity,
                pending_confirmation=plan.pending_confirmation,
            )
            conf = plan.pending_confirmation
            return await self._persist_and_return(
                message=message,
                assistant=self._reply(
                    client,
                    user_message=message or "",
                    ack=conflict_confirm_reply(conf),
                    next_question_id="",
                    catalog=catalog,
                    history=history,
                    pending_identity=pending_identity,
                    profile=profile,
                    steps_done=steps_done,
                    has_profile=True,
                    situation="conflict",
                    facts=[
                        f"field={conf.get('field')}",
                        f"old={conf.get('oldValue')!r}",
                        f"new={conf.get('newValue')!r}",
                    ],
                ),
                chat_session_id=chat_session_id,
                session=session,
                speaker_profile_id=speaker_profile_id,
                profile=profile,
                action=None,
                steps_done=steps_done,
                skipped=skipped,
                pending_identity=pending_identity,
                pending_confirmation=plan.pending_confirmation,
            )

        # Apply current-step fields + multi-answer extras that passed validation
        upsert_args = dict(plan.updates)
        if upsert_args and speaker_profile_id:
            result = await self.svc._execute_upsert(upsert_args, speaker_profile_id, jwt_user)
            action = result.get("action")
            if result.get("profile"):
                profile = result["profile"]
                profile["_id"] = str(profile.get("_id") or speaker_profile_id)
            saved = result.get("saved_fields") or []
            new_steps = steps_from_saved_fields(saved)
            current_fields = set((question.fields if question else []) or [])
            touched_current = bool(
                current_fields
                and (
                    any(f in upsert_args for f in current_fields)
                    or validation.accepted_options
                    or state.current_question_id in new_steps
                )
            )
            # Only mark current step done when this turn answered it (not a side correction).
            if state.current_question_id and touched_current:
                new_steps = merge_steps_done(new_steps, [state.current_question_id])
            steps_done = merge_steps_done(steps_done, new_steps)

            # Mixed catalog: mention rejected
            ack_parts = []
            fn = first_name((profile or {}).get("full_name") or "")
            previous_only = {
                k: v
                for k, v in upsert_args.items()
                if k not in current_fields and v not in (None, "", [])
            }
            if previous_only and not touched_current:
                ack_parts.append(previous_fields_update_ack(previous_only))
            elif validation.accepted_options and validation.rejected_options:
                ack_parts.append(off_list_ack(validation.rejected_options, validation.accepted_options))
            elif validation.accepted_options:
                ack_parts.append(
                    f"Got it{', ' + fn if fn else ''} — saved {', '.join(validation.accepted_options)}."
                )
            else:
                ack_parts.append(f"Thanks{', ' + fn if fn else ''}!")

            # Completion after testimonial
            user_answered_last = (
                state.current_question_id == "testimonial"
                or last_assistant_asked_testimonial(history)
            )
            if user_answered_last and speaker_profile_id and profile:
                profile_marked_complete, profile, steps_done = (
                    await self.svc._try_auto_mark_profile_complete(
                        speaker_profile_id, profile, steps_done, jwt_user=jwt_user
                    )
                )
                if profile_marked_complete:
                    action = "completed"
                    return await self._persist_and_return(
                        message=message,
                        assistant=completion_message(),
                        chat_session_id=chat_session_id,
                        session=session,
                        speaker_profile_id=speaker_profile_id,
                        profile=profile,
                        action=action,
                        steps_done=steps_done,
                        skipped=skipped,
                        conversation_status="COMPLETED",
                    )

            next_id = select_next_question(
                profile=profile,
                steps_done=steps_done,
                has_profile=True,
                catalog=catalog,
                history=history,
                message="",
            )
            situation = "previous_update" if previous_only and not touched_current else "answered"
            facts = None
            if previous_only and not touched_current:
                facts = [f"{k}={v}" for k, v in previous_only.items()]
            assistant = self._reply(
                client,
                user_message=message or "",
                ack=" ".join(ack_parts),
                next_question_id=next_id,
                catalog=catalog,
                history=history,
                pending_identity=pending_identity,
                profile=profile,
                steps_done=steps_done,
                has_profile=True,
                situation=situation,
                facts=facts,
            )
            return await self._persist_and_return(
                message=message,
                assistant=assistant,
                chat_session_id=chat_session_id,
                session=session,
                speaker_profile_id=speaker_profile_id,
                profile=profile,
                action=action if action != "error" else "updated",
                steps_done=steps_done,
                skipped=skipped,
                pending_identity=pending_identity,
            )

        # Nothing to save but valid somehow — re-ask
        return await self._persist_and_return(
            message=message,
            assistant=self._reply(
                client,
                user_message=message or "",
                ack="Could you share a bit more detail?",
                next_question_id=state.current_question_id,
                catalog=catalog,
                history=history,
                pending_identity=pending_identity,
                profile=profile,
                steps_done=steps_done,
                has_profile=state.has_profile,
                situation="reask",
            ),
            chat_session_id=chat_session_id,
            session=session,
            speaker_profile_id=speaker_profile_id,
            profile=profile,
            action=None,
            steps_done=steps_done,
            skipped=skipped,
            pending_identity=pending_identity,
        )

    async def _ensure_session_meta(
        self,
        *,
        chat_session_id: Optional[str],
        session: Optional[dict],
        speaker_profile_id: Optional[str],
        steps_done: Optional[List[str]] = None,
        skipped: Optional[List[str]] = None,
        pending_identity: Any = "__unset__",
        pending_confirmation: Any = "__unset__",
        conversation_status: Optional[str] = None,
    ) -> Optional[str]:
        if not chat_session_id:
            new_sess = await self.svc.chat_session_model.create_session(
                speaker_profile_id=(speaker_profile_id or ""),
                messages=[],
            )
            chat_session_id = new_sess["_id"]
            session = new_sess

        if steps_done is not None:
            await self.svc.chat_session_model.update_onboarding_steps_done(
                chat_session_id, steps_done
            )
        if skipped is not None:
            await self.svc.chat_session_model.update_skipped_questions(chat_session_id, skipped)
        if pending_identity != "__unset__":
            await self.svc.chat_session_model.update_pending_identity(
                chat_session_id, pending_identity or None
            )
        if pending_confirmation != "__unset__":
            await self.svc.chat_session_model.update_pending_confirmation(
                chat_session_id, pending_confirmation
            )
        if conversation_status:
            await self.svc.chat_session_model.update_conversation_status(
                chat_session_id, conversation_status
            )
        if speaker_profile_id:
            await self.svc.chat_session_model.update_speaker_profile_id(
                chat_session_id, speaker_profile_id
            )
        return chat_session_id

    async def _persist_and_return(
        self,
        *,
        message: str,
        assistant: str,
        chat_session_id: Optional[str],
        session: Optional[dict],
        speaker_profile_id: Optional[str],
        profile: Optional[dict],
        action: Optional[str],
        steps_done: Optional[List[str]] = None,
        skipped: Optional[List[str]] = None,
        pending_identity: Optional[Dict[str, Any]] = None,
        pending_confirmation: Any = "__unset__",
        conversation_status: Optional[str] = None,
    ) -> dict:
        chunk = [
            {"role": "user", "content": message or ""},
            {"role": "assistant", "content": assistant or ""},
        ]

        if chat_session_id and session:
            await self.svc.chat_session_model.append_messages(chat_session_id, chunk)
            out_id = chat_session_id
        elif chat_session_id:
            # Session id given but missing — try append anyway
            existing = await self.svc.chat_session_model.get_by_id(chat_session_id)
            if existing:
                await self.svc.chat_session_model.append_messages(chat_session_id, chunk)
                out_id = chat_session_id
            else:
                new_sess = await self.svc.chat_session_model.create_session(
                    speaker_profile_id=(speaker_profile_id or ""),
                    messages=chunk,
                )
                out_id = new_sess["_id"]
        else:
            new_sess = await self.svc.chat_session_model.create_session(
                speaker_profile_id=(speaker_profile_id or ""),
                messages=chunk,
            )
            out_id = new_sess["_id"]

        if steps_done is not None:
            await self.svc.chat_session_model.update_onboarding_steps_done(out_id, steps_done)
        if skipped is not None:
            await self.svc.chat_session_model.update_skipped_questions(out_id, skipped)
        if pending_identity is not None:
            await self.svc.chat_session_model.update_pending_identity(
                out_id, pending_identity or None
            )
        if pending_confirmation != "__unset__":
            await self.svc.chat_session_model.update_pending_confirmation(
                out_id, pending_confirmation
            )
        if conversation_status:
            await self.svc.chat_session_model.update_conversation_status(out_id, conversation_status)
        if speaker_profile_id:
            await self.svc.chat_session_model.update_speaker_profile_id(out_id, speaker_profile_id)

        # Persist one-time welcome flag when this turn first emitted it
        if not getattr(self, "_turn_welcome_sent", False) and speakerpitcher_welcome_in_text(
            assistant or ""
        ):
            await self.svc.chat_session_model.update_speakerpitcher_welcome_sent(out_id, True)
            self._turn_welcome_sent = True

        self.svc._catalog_name_lists = None
        return {
            "assistant_message": assistant,
            "action": action,
            "speaker_profile_id": speaker_profile_id,
            "chat_session_id": out_id,
            "profile_snapshot": profile,
        }
