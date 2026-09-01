from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from app.dependencies import get_google_query_scraper_service
from app.helpers.Utilities import Utils
from app.middleware.JWTVerification import jwt_validator
from app.schemas.GoogleQuery import GoogleQueryCreateSchema
from app.schemas.ServerResponse import ServerResponse

router = APIRouter(prefix="/api/v1/google-query-scraper", tags=["Google Query Scraper"])


@router.get("/get-all-google-queries", response_model=ServerResponse)
async def get_all_google_queries(
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    limit: int = Query(10, ge=1, le=500, description="Items per page"),
    search: Optional[str] = Query(
        None,
        description="Case-insensitive search against the Google query text",
    ),
    relatedTopics: Optional[List[str]] = Query(
        None,
        description=(
            "Filter by related topics (match any). "
            "Repeat the param and/or comma-separate values, e.g. relatedTopics=AI&relatedTopics=Marketing"
        ),
    ),
    service=Depends(get_google_query_scraper_service),
    _jwt_payload: dict = Depends(jwt_validator),
):
    """List Google queries with pagination, optional text search, and relatedTopics filter.

    Each item includes ``relatedTopics`` (API-provided, [] when absent).
    """
    try:
        result = await service.get_list(
            page=page,
            limit=limit,
            search=search,
            related_topics=relatedTopics,
        )
        return Utils.create_response(result, True)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail={"data": None, "error": str(e), "success": False},
        )


@router.post("/search", response_model=ServerResponse, status_code=201)
async def create_google_query_scrape(
    data: GoogleQueryCreateSchema,
    background_tasks: BackgroundTasks,
    service=Depends(get_google_query_scraper_service),
    jwt_payload: dict = Depends(jwt_validator),
):
    """
    Submit a Google search query for processing.
    Saves query+status=pending immediately, returns the id, and runs SERP (top-20 / 2 pages) + RapidAPI scraping in background.
    """
    try:
        query = (data.query or "").strip()
        if not query:
            raise HTTPException(
                status_code=400,
                detail={"data": None, "error": "query is required", "success": False},
            )

        user_id = jwt_payload.get("id")
        related_topics = [
            str(t).strip() for t in (data.relatedTopics or []) if str(t or "").strip()
        ]
        google_query_id = await service.create_google_query_job(
            query, user_id=user_id, related_topics=related_topics
        )
        background_tasks.add_task(service.run_query_serp_and_scrape, google_query_id, query, user_id)

        doc = await service.get_google_query_by_id(google_query_id, user_id=user_id)
        return Utils.create_response(
            {
                "googleQueryId": google_query_id,
                "query": query,
                "relatedTopics": (doc or {}).get("relatedTopics") or [],
                "status": "pending",
                "message": "Query submitted. Processing in background.",
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


@router.get("/{google_query_id}", response_model=ServerResponse)
async def get_google_query(
    google_query_id: str,
    service=Depends(get_google_query_scraper_service),
    jwt_payload: dict = Depends(jwt_validator),
):
    """Get a GoogleQueries entry by ID (status, urls, urlCollectionIds, etc)."""
    try:
        user_id = jwt_payload.get("id")
        doc = await service.get_google_query_by_id(google_query_id, user_id=user_id)
        if not doc:
            raise HTTPException(
                status_code=404,
                detail={"data": None, "error": "GoogleQuery not found", "success": False},
            )
        return Utils.create_response(doc, True)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail={"data": None, "error": str(e), "success": False},
        )


@router.delete("/delete-query/{google_query_id}", response_model=ServerResponse)
async def delete_google_query(
    google_query_id: str,
    service=Depends(get_google_query_scraper_service),
    jwt_payload: dict = Depends(jwt_validator),
):
    """Delete a Google search query by its object ID. Only the owning user can delete."""
    try:
        user_id = jwt_payload.get("id")
        deleted = await service.delete_google_query(google_query_id, user_id=user_id)
        if not deleted:
            raise HTTPException(
                status_code=404,
                detail={"data": None, "error": "GoogleQuery not found", "success": False},
            )
        return Utils.create_response(
            {"googleQueryId": google_query_id, "message": "Google query deleted successfully"},
            True,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail={"data": None, "error": str(e), "success": False},
        )

