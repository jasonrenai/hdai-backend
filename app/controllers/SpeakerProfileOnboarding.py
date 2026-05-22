"""
Speaker Profile onboarding: POST /init, POST /verify-step, POST /speaker-profile.
Stateless for init and verify-step; JWT required for final save.
"""
import logging
import os
import secrets
from typing import Optional

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import EmailStr, TypeAdapter, ValidationError

from app.config.speaker_profile_steps import get_first_step, get_next_step, get_step_by_name, is_last_step, step_to_response
from app.helpers.auth_roles import is_admin_role
from app.middleware.JWTVerification import jwt_validator
from app.models.SpeakerProfile import PROFILE_FIELDS
from app.schemas.SpeakerProfile import (
    VerifyStepRequest,
    SpeakerProfileCreateSchema,
    SpeakerProfileUpdateSchema,
    SpeakerProfileCreateFormSchema,
)
from app.services.SpeakerProfileOnboarding import (
    get_init_response,
    validate_step,
    validate_full_profile,
)
from app.services.SpeakerProfileConversation import (
    generate_recovery_message,
    generate_transition_message,
    generate_chatbot_welcome_message,
)
from app.dependencies import (
    get_auth_service,
    get_email_service,
    get_user_model,
    get_speaker_profile_model,
    get_speaker_topics_model,
    get_speaker_target_audience_model,
    get_chat_session_model,
    get_speaker_profile_chatbot_service,
)
from app.email.enums import EmailEventType
from app.helpers.SpeakerCredentialsEmail import send_speaker_credentials_email
from app.helpers.Utilities import Utils
from app.schemas.ServerResponse import ServerResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/speaker-profile", tags=["Speaker Profile Onboarding"])


async def _provision_speaker_profile_with_new_user(
    *,
    model,
    auth_service,
    profile_data: dict,
    creator_is_admin: bool = False,
    jwt_user_id: Optional[str] = None,
) -> dict:
    """
    Normalize email/full_name, reject duplicate profile email. If profile_data contains user_id,
    link to that user (after validation). Only admins may create a new users row; non-admins must
    link the profile to an existing user (typically their own JWT user id).
    Returns inserted profile document.
    """
    email_raw = profile_data.get("email")
    full_name_raw = profile_data.get("full_name")
    if email_raw is None or full_name_raw is None:
        raise HTTPException(
            status_code=400,
            detail={"data": None, "error": "email and full_name are required.", "success": False},
        )
    try:
        email = TypeAdapter(EmailStr).validate_python(str(email_raw).strip())
    except ValidationError:
        raise HTTPException(
            status_code=400,
            detail={"data": None, "error": "Invalid email address.", "success": False},
        )

    full_name = str(full_name_raw).strip()
    if len(full_name) < 2 or len(full_name) > 50:
        raise HTTPException(
            status_code=400,
            detail={
                "data": None,
                "error": "full_name must be between 2 and 50 characters.",
                "success": False,
            },
        )

    profile_data = {**profile_data, "email": email, "full_name": full_name}

    existing_user_id_raw = profile_data.pop("user_id", None)
    existing_user_id = (
        str(existing_user_id_raw).strip() if existing_user_id_raw is not None and str(existing_user_id_raw).strip() else None
    )

    if await model.get_profile_by_email(email):
        raise HTTPException(
            status_code=409,
            detail={"data": None, "error": "A speaker profile already exists for this email.", "success": False},
        )

    if existing_user_id:
        try:
            oid = ObjectId(existing_user_id)
        except InvalidId:
            raise HTTPException(
                status_code=400,
                detail={"data": None, "error": "Invalid user_id.", "success": False},
            )
        existing_user = await auth_service.user_model.get_user({"_id": oid})
        if not existing_user:
            raise HTTPException(
                status_code=404,
                detail={"data": None, "error": "No user found for the given user_id.", "success": False},
            )
        # if str(existing_user.email).strip().lower() != str(email).strip().lower():
        #     raise HTTPException(
        #         status_code=400,
        #         detail={
        #             "data": None,
        #             "error": "email must match the account for the given user_id.",
        #             "success": False,
        #         },
        #     )
        doc = await model.create_speaker_profile(existing_user_id, profile_data)
        return doc

    if not creator_is_admin:
        link_uid = (jwt_user_id or "").strip()
        if not link_uid:
            raise HTTPException(
                status_code=403,
                detail={
                    "data": None,
                    "error": "Only admins can create a new user for a speaker profile. "
                    "Provide user_id or sign in as admin.",
                    "success": False,
                },
            )
        try:
            ObjectId(link_uid)
        except InvalidId:
            raise HTTPException(
                status_code=400,
                detail={"data": None, "error": "Invalid user id.", "success": False},
            )
        link_user = await auth_service.user_model.get_user({"_id": ObjectId(link_uid)})
        if not link_user:
            raise HTTPException(
                status_code=404,
                detail={"data": None, "error": "No user found for the logged-in account.", "success": False},
            )
        return await model.create_speaker_profile(link_uid, profile_data)

    if await auth_service.user_model.get_user({"email": email}):
        raise HTTPException(
            status_code=409,
            detail={"data": None, "error": "A user with this email already exists.", "success": False},
        )

    plain_password = secrets.token_urlsafe(12)
    created = await auth_service.create_speaker_user(
        email=email,
        full_name=full_name,
        plain_password=plain_password,
    )
    if not created.get("success"):
        raise HTTPException(
            status_code=400,
            detail={
                "data": None,
                "error": created.get("error", "Could not create user account."),
                "success": False,
            },
        )

    new_user_id = created["user_id"]
    doc = await model.create_speaker_profile(new_user_id, profile_data)
    send_speaker_credentials_email(email, full_name, plain_password)
    return doc


@router.get("/get-speaker-profiles", response_model=ServerResponse)
async def get_my_speaker_profiles(
    user_id: Optional[str] = Query(
        None,
        description="If set, return speaker profiles for this user (must match the logged-in user or caller must be admin).",
    ),
    jwt_payload: dict = Depends(jwt_validator),
    model=Depends(get_speaker_profile_model),
):
    """
    Without user_id: admins get all profiles; other users get their own (newest first).
    With user_id: returns all profiles linked to that user; non-admins may only pass their own id.
    """
    try:
        token_user_id = jwt_payload.get("id") or jwt_payload.get("user_id")
        if not token_user_id:
            raise HTTPException(
                status_code=401,
                detail={"data": None, "error": "User ID not found in token.", "success": False},
            )
        if user_id is not None:
            uid = (user_id or "").strip()
            if not uid:
                raise HTTPException(
                    status_code=400,
                    detail={"data": None, "error": "user_id cannot be empty.", "success": False},
                )
            is_admin = is_admin_role(jwt_payload.get("userType"))
            if not is_admin and str(token_user_id) != str(uid):
                raise HTTPException(
                    status_code=403,
                    detail={"data": None, "error": "You can only access your own speaker profiles.", "success": False},
                )
            profiles = await model.get_profiles_by_user_id(uid)
            return Utils.create_response(profiles, True)
        if is_admin_role(jwt_payload.get("userType")):
            profiles = await model.get_all_profiles()
        else:
            profiles = await model.get_profiles_by_user_id(str(token_user_id))
        return Utils.create_response(profiles, True)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail={"data": None, "error": str(e), "success": False},
        )


@router.get("/get-speaker-profiles-by-user", response_model=ServerResponse)
async def get_speaker_profiles_by_user_id(
    user_id: str = Query(..., description="User id whose speaker profiles to return"),
    jwt_payload: dict = Depends(jwt_validator),
    model=Depends(get_speaker_profile_model),
):
    """
    Return every speaker profile linked to the given user (newest first; not paginated).
    Authenticated users may only request their own user_id; admins may request any user_id.
    """
    try:
        user_id = (user_id or "").strip()
        if not user_id:
            raise HTTPException(
                status_code=400,
                detail={"data": None, "error": "user_id is required.", "success": False},
            )
        token_user_id = jwt_payload.get("id") or jwt_payload.get("user_id")
        if not token_user_id:
            raise HTTPException(
                status_code=401,
                detail={"data": None, "error": "User ID not found in token.", "success": False},
            )
        is_admin = is_admin_role(jwt_payload.get("userType"))
        if not is_admin and str(token_user_id) != str(user_id):
            raise HTTPException(
                status_code=403,
                detail={"data": None, "error": "You can only access your own speaker profiles.", "success": False},
            )
        profiles = await model.get_profiles_by_user_id(user_id)
        return Utils.create_response(profiles, True)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail={"data": None, "error": str(e), "success": False},
        )


@router.get("/users/id-and-full-name", response_model=ServerResponse)
async def list_users_id_and_full_name(
    jwt_payload: dict = Depends(jwt_validator),
    user_model=Depends(get_user_model),
):
    """
    Return all users with only id and fullName (Mongo projection: _id + fullName only).
    The list is not filtered by the caller (same for any authenticated user).
    """
    try:
        token_user_id = jwt_payload.get("id") or jwt_payload.get("user_id")
        if not token_user_id:
            raise HTTPException(
                status_code=401,
                detail={"data": None, "error": "User ID not found in token.", "success": False},
            )
        users = await user_model.get_all_user_ids_and_full_names()
        return Utils.create_response(users, True)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail={"data": None, "error": str(e), "success": False},
        )


@router.get("/{profile_id}", response_model=ServerResponse)
async def get_speaker_profile_by_id(
    profile_id: str,
    jwt_payload: dict = Depends(jwt_validator),
    model=Depends(get_speaker_profile_model),
):
    """
    Get a speaker profile by id. Admins may read any profile; other users only if the profile belongs to them.
    """
    try:
        token_user_id = jwt_payload.get("id") or jwt_payload.get("user_id")
        if not token_user_id:
            raise HTTPException(
                status_code=401,
                detail={"data": None, "error": "User ID not found in token.", "success": False},
            )
        profile = await model.get_profile(profile_id)
        if not profile:
            raise HTTPException(
                status_code=404,
                detail={"data": None, "error": "Profile not found.", "success": False},
            )
        is_admin = is_admin_role(jwt_payload.get("userType"))
        owner_id = profile.get("user_id")
        if not is_admin and (owner_id is None or str(owner_id) != str(token_user_id)):
            raise HTTPException(
                status_code=403,
                detail={"data": None, "error": "You do not have access to this profile.", "success": False},
            )
        return Utils.create_response(profile, True)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail={"data": None, "error": str(e), "success": False},
        )


@router.put("/{profile_id}", response_model=ServerResponse)
async def update_speaker_profile(
    profile_id: str,
    body: SpeakerProfileUpdateSchema,
    jwt_payload: dict = Depends(jwt_validator),
    model=Depends(get_speaker_profile_model),
):
    """
    Update a speaker profile. Admins may update any profile; other users only their own.
    """
    token_user_id = jwt_payload.get("id") or jwt_payload.get("user_id")
    if not token_user_id:
        raise HTTPException(
            status_code=401,
            detail={"data": None, "error": "User ID not found in token.", "success": False},
        )
    profile = await model.get_profile(profile_id)
    if not profile:
        raise HTTPException(
            status_code=404,
            detail={"data": None, "error": "Profile not found.", "success": False},
        )
    is_admin = is_admin_role(jwt_payload.get("userType"))
    owner_id = profile.get("user_id")
    if not is_admin and (owner_id is None or str(owner_id) != str(token_user_id)):
        raise HTTPException(
            status_code=403,
            detail={"data": None, "error": "You do not have access to this profile.", "success": False},
        )
    updates = body.model_dump(exclude_unset=True, by_alias=True)
    if not updates:
        return Utils.create_response(profile, True)
    updated = await model.update_profile(profile_id, updates)
    if not updated:
        raise HTTPException(
            status_code=500,
            detail={"data": None, "error": "Update failed.", "success": False},
        )
    return Utils.create_response(updated, True)


@router.delete("/delete-speaker-profile/{profile_id}", response_model=ServerResponse)
async def delete_speaker_profile(
    profile_id: str,
    jwt_payload: dict = Depends(jwt_validator),
    model=Depends(get_speaker_profile_model),
    chat_session_model=Depends(get_chat_session_model),
):
    """
    Delete a speaker profile by id. Also removes chat sessions tied to that profile.
    Admins may delete any profile; other users only their own.
    """
    try:
        token_user_id = jwt_payload.get("id") or jwt_payload.get("user_id")
        if not token_user_id:
            raise HTTPException(
                status_code=401,
                detail={"data": None, "error": "User ID not found in token.", "success": False},
            )
        profile = await model.get_profile(profile_id)
        if not profile:
            raise HTTPException(
                status_code=404,
                detail={"data": None, "error": "Profile not found.", "success": False},
            )
        is_admin = is_admin_role(jwt_payload.get("userType"))
        owner_id = profile.get("user_id")
        if not is_admin and (owner_id is None or str(owner_id) != str(token_user_id)):
            raise HTTPException(
                status_code=403,
                detail={"data": None, "error": "You do not have access to this profile.", "success": False},
            )
        deleted = await model.delete_profile(profile_id)
        if not deleted:
            raise HTTPException(
                status_code=500,
                detail={"data": None, "error": "Delete failed.", "success": False},
            )
        await chat_session_model.delete_by_speaker_profile_id(profile_id)
        return Utils.create_response(
            {"_id": profile_id, "message": "Speaker profile deleted successfully."},
            True,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail={"data": None, "error": str(e), "success": False},
        )


@router.post("/create-speaker-profile", response_model=ServerResponse, status_code=201)
async def create_speaker_profile(
    body: SpeakerProfileCreateFormSchema,
    jwt_payload: dict = Depends(jwt_validator),
    model=Depends(get_speaker_profile_model),
    auth_service=Depends(get_auth_service),
):
    """
    Create a new speaker profile in one shot using a form-style payload (no conversational AI / stepwise onboarding).
    Requires email and full_name. If optional user_id is provided, the profile is linked to that user.
    Only admins may create a new platform user; other roles link the profile to their account or a given user_id.
    """
    user_id = jwt_payload.get("id") or jwt_payload.get("user_id")
    if not user_id:
        raise HTTPException(
            status_code=401,
            detail={"data": None, "error": "User ID not found in token.", "success": False},
        )

    profile_data = body.model_dump(exclude_unset=True, by_alias=True)
    doc = await _provision_speaker_profile_with_new_user(
        model=model,
        auth_service=auth_service,
        profile_data=profile_data,
        creator_is_admin=is_admin_role(jwt_payload.get("userType")),
        jwt_user_id=str(user_id),
    )
    return Utils.create_response({"id": str(doc["_id"]), "profile": doc}, True)


@router.get("/{profile_id}/resume-onboarding")
async def resume_onboarding(
    profile_id: str,
    jwt_payload: dict = Depends(jwt_validator),
    model=Depends(get_speaker_profile_model),
    speaker_topics_model=Depends(get_speaker_topics_model),
    speaker_target_audience_model=Depends(get_speaker_target_audience_model),
):
    """
    Resume onboarding: return stored conversation and current step payload.
    FE renders conversation as chat, shows step as form, then calls POST /verify-step as in create-profile.
    Admins may resume any profile; other users only their own.
    """
    token_user_id = jwt_payload.get("id") or jwt_payload.get("user_id")
    if not token_user_id:
        raise HTTPException(
            status_code=401,
            detail={"data": None, "error": "User ID not found in token.", "success": False},
        )
    profile = await model.get_profile(profile_id)
    if not profile:
        raise HTTPException(
            status_code=404,
            detail={"data": None, "error": "Profile not found.", "success": False},
        )
    is_admin = is_admin_role(jwt_payload.get("userType"))
    owner_id = profile.get("user_id")
    if not is_admin and (owner_id is None or str(owner_id) != str(token_user_id)):
        raise HTTPException(
            status_code=403,
            detail={"data": None, "error": "You do not have access to this profile.", "success": False},
        )
    conversation = profile.get("conversation") or []
    current_step_name = profile.get("current_step")
    if current_step_name:
        step_def = get_step_by_name(current_step_name)
        step_payload = await _step_payload_with_dynamic_allowed(
            step_def, speaker_topics_model, speaker_target_audience_model
        )
        # Generate question for current step like verify-step: use AI transition from last completed step.
        completed_steps = profile.get("completed_steps") or []
        if completed_steps and step_payload:
            last_step_name = completed_steps[-1]
            last_answer = profile.get(last_step_name)
            if last_answer is not None:
                ai_question = generate_transition_message(
                    step_name=last_step_name,
                    normalized_answer=last_answer,
                    next_step=step_payload,
                    is_last_step=False,
                )
                step_payload = {**step_payload, "question": ai_question}
    else:
        step_payload = None
    is_complete = current_step_name is None
    return {
        "profile_id": profile_id,
        "is_complete": is_complete,
        "completed_steps": profile.get("completed_steps") or [],
        "conversation": conversation,
        "next_step": step_payload,
    }


@router.post("/init")
async def init_onboarding():
    """
    Start the flow: return the first step metadata.
    No auth, no session, no DB.
    """
    return get_init_response()


@router.post("/init-chatbot")
async def init_chatbot_onboarding():
    """
    Start chatbot-based speaker profile flow. No input required.
    Returns welcome message asking for professional name, title, and company (same as first /chat turn).
    """
    welcome = generate_chatbot_welcome_message()
    return {"assistant_message": welcome}


@router.post("/chat")
async def speaker_profile_chat(
    body: dict,
    request: Request,
    chatbot_service=Depends(get_speaker_profile_chatbot_service),
):
    """
    Chat API for speaker profile creation/update via conversation.
    Body shape:
      {
        "message": "user message as string",
        "chat_session_id": "optional existing chat session id"
      }
    Flow: name + title + company, then email + phone (profile created), then location, social, bio,
    preferred speaking time, catalog fields, and remaining questions. See SpeakerProfileChatbotService.process_chat.
    JWT optional: when provided, user_id is linked to the profile.
    """
    jwt_user = None
    auth = request.headers.get("Authorization")
    if auth and auth.startswith("Bearer "):
        try:
            from fastapi.security import HTTPAuthorizationCredentials

            creds = HTTPAuthorizationCredentials(
                scheme="Bearer", credentials=auth[7:].strip()
            )
            jwt_user = jwt_validator(creds)
        except Exception:
            pass

    message = body.get("message") or ""
    chat_session_id = body.get("chat_session_id")

    result = await chatbot_service.process_chat(
        message=message,
        chat_session_id=chat_session_id,
        jwt_user=jwt_user,
    )
    return Utils.create_response(result, True)


@router.get("/chat-sessions/by-profile/{speaker_profile_id}", response_model=ServerResponse)
async def get_chat_sessions_by_profile(
    speaker_profile_id: str,
    model=Depends(get_chat_session_model),
):
    """
    Get all chat sessions for a given speaker profile id (newest first).
    """
    sessions = await model.get_by_profile_id(speaker_profile_id)
    return Utils.create_response(sessions, True)


@router.get("/chat-sessions/{chat_session_id}", response_model=ServerResponse)
async def get_chat_session_by_id(
    chat_session_id: str,
    model=Depends(get_chat_session_model),
):
    """
    Get a single chat session by id (includes full conversation).
    """
    session = await model.get_by_id(chat_session_id)
    if not session:
        raise HTTPException(
            status_code=404,
            detail={"data": None, "error": "Chat session not found.", "success": False},
        )
    return Utils.create_response(session, True)


def _profile_context(profile: dict) -> dict:
    """Small summary for context-aware validation (inform, do not strictly gate)."""
    if not profile:
        return {}
    return {k: profile.get(k) for k in PROFILE_FIELDS if profile.get(k) is not None and profile.get(k) != [] and profile.get(k) != ""}


async def _step_payload_with_dynamic_allowed(step_def, speaker_topics_model, speaker_target_audience_model):
    """Build step payload; for topics/target_audiences steps, fetch and inject allowed_values from DB."""
    payload = step_to_response(step_def) if step_def else {}
    if step_def and step_def.step_name == "topics":
        payload["allowed_values"] = await speaker_topics_model.get_all()
    elif step_def and step_def.step_name == "target_audiences":
        payload["allowed_values"] = await speaker_target_audience_model.get_all()
    return payload


def _allowed_values_for_recovery(step_name, step_def, allowed_topics_for_step, allowed_target_audiences_for_step):
    """Return allowed_values for generate_recovery_message based on current step."""
    if step_name == "topics":
        return allowed_topics_for_step
    if step_name == "target_audiences":
        return allowed_target_audiences_for_step
    return step_def.allowed_values if step_def else None


@router.post("/verify-step")
async def verify_step(
    body: VerifyStepRequest,
    jwt_payload: dict = Depends(jwt_validator),
    model=Depends(get_speaker_profile_model),
    speaker_topics_model=Depends(get_speaker_topics_model),
    speaker_target_audience_model=Depends(get_speaker_target_audience_model),
):
    """
    Validate and normalize the answer for the given step; return next step or repeat.
    Progressive save: first valid step (full_name) creates profile and returns profile_id; subsequent steps require profile_id.
    """
    user_id = jwt_payload.get("id") or jwt_payload.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="User ID not found in token.")
    step_def = get_step_by_name(body.step)
    validation_mode = step_def.validation_mode if step_def else "unknown"

    # When step is topics or target_audiences, fetch allowed values once (for validation and for repeat/next payloads)
    allowed_topics_for_step = None
    allowed_target_audiences_for_step = None
    if body.step == "topics":
        allowed_topics_for_step = await speaker_topics_model.get_all()
    elif body.step == "target_audiences":
        allowed_target_audiences_for_step = await speaker_target_audience_model.get_all()

    if body.step != "full_name" and not body.profile_id:
        repeat_step = await _step_payload_with_dynamic_allowed(step_def, speaker_topics_model, speaker_target_audience_model)
        assistant_message = generate_recovery_message(
            step_name=body.step,
            user_answer=body.answer,
            reason_code="MISSING_PROFILE_ID",
            retry_count=body.retry_count or 0,
            allowed_values=_allowed_values_for_recovery(body.step, step_def, allowed_topics_for_step, allowed_target_audiences_for_step),
        )
        return {"assistant_message": assistant_message, "repeat_step": repeat_step}

    profile = None
    expected_step_name = get_first_step().step_name
    if body.profile_id:
        profile = await model.get_profile(body.profile_id)
        if profile:
            expected_step_name = profile.get("current_step") or expected_step_name
    profile_context = _profile_context(profile) if profile else None

    result = validate_step(
        step_name=body.step,
        answer=body.answer,
        source=body.source,
        expected_step_name=expected_step_name,
        profile_context=profile_context,
        allowed_topics_for_step=allowed_topics_for_step,
        allowed_target_audiences_for_step=allowed_target_audiences_for_step,
    )

    # Debug logging: verify branch logic
    def _safe_repr(v, max_len=80):
        if v is None:
            return "None"
        s = str(v)
        return s[:max_len] + "..." if len(s) > max_len else s

    logger.info(
        "verify-step: step=%s validation_mode=%s status=%s reason_code=%s normalized=%s",
        body.step,
        validation_mode,
        result.get("status"),
        result.get("reason_code"),
        _safe_repr(result.get("normalized_value")),
    )

    if result.get("status") != "VALID":
        logger.info("verify-step: branch=repeat")
        repeat_step = await _step_payload_with_dynamic_allowed(step_def, speaker_topics_model, speaker_target_audience_model)
        assistant_message = generate_recovery_message(
            step_name=body.step,
            user_answer=body.answer,
            reason_code=result.get("reason_code") or "UNKNOWN",
            retry_count=body.retry_count or 0,
            allowed_values=_allowed_values_for_recovery(body.step, step_def, allowed_topics_for_step, allowed_target_audiences_for_step),
        )
        return {"assistant_message": assistant_message, "repeat_step": repeat_step}

    logger.info("verify-step: branch=success")
    normalized = result.get("normalized_value")
    # When optional step is skipped (normalized is None), use user's typed answer for conversation and response; profile field stays null/empty
    if normalized is None and body.answer is not None and body.step in (
        "linkedin_url",
        "past_speaking_examples",
        "video_links",
        "testimonial",
        "key_takeaways",
        "talk_description",
    ):
        user_answer_str = (
            body.answer if isinstance(body.answer, str) else " ".join(str(x).strip() for x in body.answer if x)
        )
        display_value = user_answer_str.strip() or None
    else:
        display_value = normalized

    next_step_def = get_next_step(body.step)
    next_step_name = next_step_def.step_name if next_step_def else None
    is_last = is_last_step(body.step)
    next_step_payload = await _step_payload_with_dynamic_allowed(next_step_def, speaker_topics_model, speaker_target_audience_model) if next_step_def else None
    if is_last:
        next_step_payload = {}
    assistant_message = generate_transition_message(
        step_name=body.step,
        normalized_answer=display_value,
        next_step=next_step_payload,
        is_last_step=is_last,
    )

    # Agent message for the step just completed: first step uses config question; later steps use the AI transition we stored last time.
    agent_message_for_step = (
        profile.get("last_assistant_message") if (profile and body.step != "full_name") else None
    ) or step_def.question

    profile_id = body.profile_id
    if body.step == "full_name" and not body.profile_id:
        doc = await model.create_profile(normalized, user_id=user_id)
        profile_id = str(doc["_id"])
        await model.append_conversation(profile_id, agent_message_for_step, normalized)
        await model.update_last_assistant_message(profile_id, assistant_message)
    elif body.profile_id and profile:
        completed = list(profile.get("completed_steps") or [])
        if body.step not in completed:
            completed.append(body.step)
        await model.append_conversation(profile_id, agent_message_for_step, display_value)
        # past_speaking_examples and video_links are stored as arrays; when skipped (normalized None) store []
        if body.step == "past_speaking_examples":
            if normalized is None:
                step_updates = {body.step: []}
            elif isinstance(normalized, list):
                step_updates = {body.step: normalized}
            else:
                step_updates = {body.step: [normalized]}
        elif body.step == "video_links":
            step_updates = {body.step: normalized if normalized is not None else []}
        elif body.step == "linkedin_url":
            if normalized is None:
                step_updates = {}
            elif isinstance(normalized, dict):
                step_updates = {
                    k: v
                    for k, v in normalized.items()
                    if k in ("linkedin_url", "facebook", "twitter", "instagram") and v
                }
            else:
                step_updates = {"linkedin_url": normalized}
        else:
            step_updates = {body.step: normalized}
        await model.update_step(
            body.profile_id,
            updates=step_updates,
            next_step_name=next_step_name,
            completed_steps=completed,
            last_assistant_message=assistant_message,
        )
        if is_last:
            to_email = profile.get("email")
            full_name = (profile.get("full_name") or "").strip()
            app_base = os.getenv("API_BASE_URL", "https://app.speakerpitcher.ai").rstrip("/")
            if to_email:
                try:
                    get_email_service().send_event_email(
                        event_type=EmailEventType.SYSTEM_NOTIFICATION,
                        to_email=str(to_email).strip(),
                        template_model={
                            "hero_image_url": "",
                            "update_title": "Profile complete",
                            "user_name": full_name or "there",
                            "intro_message": "Thank you for completing your speaker profile.",
                            "feature_title": "What happens next",
                            "feature_description": "We will match you with speaking opportunities that fit your expertise.",
                            "body_message": "You can update your profile anytime from your dashboard.",
                            "cta_url": app_base,
                            "cta_text": "Open SpeakerPitcher",
                        },
                    )
                except Exception as e:
                    logger.warning("Failed to send onboarding-complete email: %s", e)
            else:
                logger.warning("Skipping onboarding-complete email: missing profile email")
        profile_id = body.profile_id

    return {
        "assistant_message": assistant_message,
        "normalized_answer": display_value,
        "next_step": next_step_payload,
        "is_last_step": is_last,
        "profile_id": profile_id,
    }


@router.post("", status_code=201)
async def save_speaker_profile(
    body: SpeakerProfileCreateSchema,
    jwt_payload: dict = Depends(jwt_validator),
    model=Depends(get_speaker_profile_model),
    speaker_topics_model=Depends(get_speaker_topics_model),
    speaker_target_audience_model=Depends(get_speaker_target_audience_model),
    auth_service=Depends(get_auth_service),
):
    """
    Save the full speaker profile after onboarding. Requires JWT.
    Re-runs full validation pipeline for all fields before saving.
    Deprecated: Progressive save per step is preferred; this endpoint kept for final re-validation, publishing, or workflow extensions.
    """
    user_id = jwt_payload.get("id") or jwt_payload.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="User ID not found in token.")
    
    allowed_topics = await speaker_topics_model.get_all()
    allowed_target_audiences = await speaker_target_audience_model.get_all()
    # Re-validate entire profile (authoritative validation)
    validation_errors = validate_full_profile(
        body.model_dump(by_alias=True),
        allowed_topics=allowed_topics,
        allowed_target_audiences=allowed_target_audiences,
    )
    if validation_errors:
        raise HTTPException(
            status_code=400,
            detail={
                "assistant_message": "We couldn't save your profile—something doesn't look quite right. Please go through the steps again and make sure everything is filled in."
            }
        )
    
    profile_data = body.model_dump(by_alias=True)
    doc = await _provision_speaker_profile_with_new_user(
        model=model,
        auth_service=auth_service,
        profile_data=profile_data,
        creator_is_admin=is_admin_role(jwt_payload.get("userType")),
        jwt_user_id=str(user_id),
    )
    return {"success": True, "id": str(doc["_id"])}
