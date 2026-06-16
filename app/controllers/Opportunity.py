"""Controller for Opportunities and speaker outreach email content."""

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Query
from app.schemas.ServerResponse import ServerResponse
from app.schemas.Opportunity import (
    GenerateOpportunityApplicationContentSchema,
    GenerateOpportunityEmailContentSchema,
    OpportunityActivityUpdateSchema,
)
from app.helpers.Utilities import Utils
from app.middleware.JWTVerification import jwt_validator
from app.dependencies import (
    get_opportunity_service,
    get_matched_opportunities_email_service,
    get_opportunity_email_content_service,
    get_opportunity_application_content_service,
    get_opportunity_activity_service,
)

router = APIRouter(prefix="/api/v1/opportunities", tags=["Opportunities"])


@router.get("/", response_model=ServerResponse)
async def list_opportunities(
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    limit: int = Query(10, ge=1, le=100, description="Items per page"),
    sort_by_start_date: str = Query(None, description="Sort by start_date: asc or desc"),
    sort_by_end_date: str = Query(None, description="Sort by end_date: asc or desc"),
    sort_by_created_at: str = Query(None, description="Sort by created_at: asc or desc"),
    service=Depends(get_opportunity_service),
    jwt_payload: dict = Depends(jwt_validator),
):
    """List opportunities with pagination. Optional sort by start_date, end_date, and/or created_at (asc | desc)."""
    try:
        result = await service.list_opportunities(
            page=page,
            limit=limit,
            sort_by_start_date=sort_by_start_date,
            sort_by_end_date=sort_by_end_date,
            sort_by_created_at=sort_by_created_at,
        )
        return Utils.create_response(result, True)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail={"data": None, "error": str(e), "success": False},
        )


@router.get("/match-by-speaker", response_model=ServerResponse)
async def match_opportunities_by_speaker(
    background_tasks: BackgroundTasks,
    speaker_profile_id: str = Query(..., description="Speaker profile ID"),
    service=Depends(get_opportunity_service),
    jwt_payload: dict = Depends(jwt_validator),
):
    """
    Delete existing matchedOpportunities for this speaker, create an entry with status 'processing',
    start a background job to match opportunities, then return the entry id.
    On completion the background task updates that entry to status 'completed' with the matched opportunity ids.
    Use GET /opportunities/matched?speaker_profile_id=... to fetch results (status in doc when needed).
    """
    try:
        entry_id = await service.start_matching_run(speaker_profile_id)
        if not entry_id:
            raise HTTPException(
                status_code=500,
                detail={"data": None, "error": "Failed to create matching entry", "success": False},
            )
        background_tasks.add_task(
            service.run_matching_and_save,
            speaker_profile_id,
            None,  # match_agent
            entry_id,
        )
        return Utils.create_response(
            {
                "message": "Matching started",
                "speaker_profile_id": speaker_profile_id,
                "matched_opportunities_entry_id": entry_id,
            },
            True,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail={"data": None, "error": str(e), "success": False},
        )


@router.post("/send-matched-email", response_model=ServerResponse)
async def send_matched_opportunities_email(
    speaker_profile_id: str = Query(..., description="Speaker profile ID"),
    service=Depends(get_matched_opportunities_email_service),
    jwt_payload: dict = Depends(jwt_validator),
):
    """
    Send matched opportunities email (Postmark New_opportunity template: user_name + opportunities[]).
    Recipient is the contact email on the speaker profile. Matching may trigger this when a run completes.
    """
    try:
        sent = await service.send_matched_opportunities_email(speaker_profile_id)
        if not sent:
            raise HTTPException(
                status_code=400,
                detail={
                    "data": None,
                    "error": "Could not send email (missing profile/email, no matched opportunities, or Postmark config)",
                    "success": False,
                },
            )
        return Utils.create_response(
            {"message": "Matched opportunities email sent", "speaker_profile_id": speaker_profile_id},
            True,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail={"data": None, "error": str(e), "success": False},
        )


@router.post("/email-content/generate", response_model=ServerResponse)
async def generate_opportunity_email_content(
    body: GenerateOpportunityEmailContentSchema = Body(...),
    service=Depends(get_opportunity_email_content_service),
    jwt_payload: dict = Depends(jwt_validator),
):
    """
    Generate professional outreach email title/content for a speaker and opportunity,
    then save it to EmailContent collection.

    Response includes mail_title/mail_content (AI outreach copy), recipient_email,
    event_contact (organizer email), and submission_note when the opportunity requires email submission.

    Body field `type` (string slug) selects the authority angle: association_membership,
    experience_expertise, or case_study_results — each uses a dedicated system prompt.
    """
    try:
        created = await service.generate_and_save_email_content(
            speaker_profile_id=body.speaker_profile_id,
            opportunity_id=body.opportunity_id,
            user_suggestion_prompt=body.user_suggestion_prompt,
            authority_type=body.authority_type,
        )
        return Utils.create_response(created, True)
    except ValueError as ve:
        raise HTTPException(
            status_code=400,
            detail={"data": None, "error": str(ve), "success": False},
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={"data": None, "error": str(e), "success": False},
        )


@router.get("/email-content", response_model=ServerResponse)
async def get_opportunity_email_content(
    speaker_profile_id: str = Query(..., description="Speaker profile ID"),
    opportunity_id: str = Query(..., description="Opportunity ID"),
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    limit: int = Query(10, ge=1, le=100, description="Items per page"),
    service=Depends(get_opportunity_email_content_service),
    jwt_payload: dict = Depends(jwt_validator),
):
    """List generated outreach emails by speaker and opportunity with pagination.

    Each item includes mail_title/mail_content plus saved recipient_email, event_contact email,
    and submission_note when available.
    """
    try:
        result = await service.list_email_content(
            speaker_profile_id=speaker_profile_id,
            opportunity_id=opportunity_id,
            page=page,
            limit=limit,
        )
        return Utils.create_response(result, True)
    except ValueError as ve:
        raise HTTPException(
            status_code=400,
            detail={"data": None, "error": str(ve), "success": False},
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={"data": None, "error": str(e), "success": False},
        )


@router.post("/application-content/generate", response_model=ServerResponse)
async def generate_opportunity_application_content(
    body: GenerateOpportunityApplicationContentSchema = Body(...),
    service=Depends(get_opportunity_application_content_service),
    jwt_payload: dict = Depends(jwt_validator),
):
    """
    Generate speaker application form fields for an opportunity and save to ApplicationContent.

    Static fields (name, title, company, email, bio) come from the speaker profile.
    AI-generated fields (presentation_type, session_title, abstract, takeaways, speaking_history)
    are tailored to the opportunity.
    """
    try:
        created = await service.generate_and_save_application_content(
            speaker_profile_id=body.speaker_profile_id,
            opportunity_id=body.opportunity_id,
        )
        return Utils.create_response(created, True)
    except ValueError as ve:
        raise HTTPException(
            status_code=400,
            detail={"data": None, "error": str(ve), "success": False},
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={"data": None, "error": str(e), "success": False},
        )


@router.get("/application-content", response_model=ServerResponse)
async def get_opportunity_application_content(
    speaker_profile_id: str = Query(..., description="Speaker profile ID"),
    opportunity_id: str = Query(..., description="Opportunity ID"),
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    limit: int = Query(10, ge=1, le=100, description="Items per page"),
    service=Depends(get_opportunity_application_content_service),
    jwt_payload: dict = Depends(jwt_validator),
):
    """List generated application content by speaker and opportunity with pagination."""
    try:
        result = await service.list_application_content(
            speaker_profile_id=speaker_profile_id,
            opportunity_id=opportunity_id,
            page=page,
            limit=limit,
        )
        return Utils.create_response(result, True)
    except ValueError as ve:
        raise HTTPException(
            status_code=400,
            detail={"data": None, "error": str(ve), "success": False},
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={"data": None, "error": str(e), "success": False},
        )


@router.get("/matched", response_model=ServerResponse)
async def get_matched_opportunities_by_speaker(
    speaker_profile_id: str = Query(..., description="Speaker profile ID"),
    service=Depends(get_opportunity_service),
    jwt_payload: dict = Depends(jwt_validator),
):
    """
    Get matched opportunities for a speaker from the matchedOpportunities collection.
    Returns full opportunity documents whose ids are in the saved opportunities array for this speaker.
    """
    try:
        opportunities, status = await service.get_matched_opportunities_by_speaker_id(
            speaker_profile_id
        )
        return Utils.create_response(
            {"opportunities": opportunities, "status": status}, True
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail={"data": None, "error": str(e), "success": False},
        )


@router.get("/activity", response_model=ServerResponse)
async def get_opportunity_activity(
    opportunityId: str = Query(..., description="Opportunity ID"),
    speaker_id: str = Query(..., description="Speaker ID (speaker profile)"),
    service=Depends(get_opportunity_activity_service),
    jwt_payload: dict = Depends(jwt_validator),
):
    """Get wishlist / applied / accepted / expired flags and optional user outcomes for this speaker and opportunity."""
    try:
        result = await service.get_activity(speaker_id, opportunityId)
        return Utils.create_response(result, True)
    except ValueError as ve:
        raise HTTPException(
            status_code=400,
            detail={"data": None, "error": str(ve), "success": False},
        )
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail={"data": None, "error": str(e), "success": False},
        )


@router.patch("/activity", response_model=ServerResponse)
async def patch_opportunity_activity(
    body: OpportunityActivityUpdateSchema = Body(...),
    service=Depends(get_opportunity_activity_service),
    jwt_payload: dict = Depends(jwt_validator),
):
    """Create or update opportunity activity flags (only fields sent are changed). Send outcomes: null to clear stored outcomes."""
    try:
        result = await service.update_activity(
            speaker_id=body.speaker_id,
            opportunity_id=body.opportunityId,
            is_wishlist=body.isWishlist,
            is_applied=body.isApplied,
            is_accepted=body.isAccepted,
            is_expired=body.isExpired,
            is_archived=body.isArchived,
            outcomes=body.outcomes,
            outcomes_provided="outcomes" in body.model_fields_set,
        )
        return Utils.create_response(result, True)
    except ValueError as ve:
        raise HTTPException(
            status_code=400,
            detail={"data": None, "error": str(ve), "success": False},
        )
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail={"data": None, "error": str(e), "success": False},
        )


@router.get("/{opportunity_id}", response_model=ServerResponse)
async def get_opportunity_by_id(
    opportunity_id: str,
    service=Depends(get_opportunity_service),
    jwt_payload: dict = Depends(jwt_validator),
):
    """Get a single opportunity by ID. Link in emails points to this API."""
    try:
        opportunity = await service.get_opportunity_by_id(opportunity_id)
        if not opportunity:
            raise HTTPException(
                status_code=404,
                detail={"data": None, "error": "Opportunity not found", "success": False},
            )
        return Utils.create_response(opportunity, True)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail={"data": None, "error": str(e), "success": False},
        )


@router.delete("/{opportunity_id}", response_model=ServerResponse)
async def delete_opportunity(
    opportunity_id: str,
    service=Depends(get_opportunity_service),
    jwt_payload: dict = Depends(jwt_validator),
):
    """Delete an opportunity by ID."""
    try:
        deleted = await service.delete_opportunity(opportunity_id)
        if not deleted:
            raise HTTPException(
                status_code=404,
                detail={"data": None, "error": "Opportunity not found", "success": False},
            )
        return Utils.create_response({"message": "Opportunity deleted successfully"}, True)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail={"data": None, "error": str(e), "success": False},
        )
