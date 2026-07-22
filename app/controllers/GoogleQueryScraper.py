from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from app.dependencies import get_google_query_scraper_service
from app.helpers.Utilities import Utils
from app.middleware.JWTVerification import jwt_validator
from app.schemas.GoogleQuery import GoogleQueryCreateSchema
from app.schemas.ServerResponse import ServerResponse

router = APIRouter(prefix="/api/v1/google-query-scraper", tags=["Google Query Scraper"])


@router.get("/get-all-google-queries", response_model=ServerResponse)
async def get_all_google_queries(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    service=Depends(get_google_query_scraper_service),
    _jwt_payload: dict = Depends(jwt_validator),
):
    """List all Google queries with pagination."""
    try:
        result = await service.get_list(skip=skip, limit=limit)
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
        google_query_id = await service.create_google_query_job(query, user_id=user_id)
        background_tasks.add_task(service.run_query_serp_and_scrape, google_query_id, query, user_id)

        return Utils.create_response(
            {
                "googleQueryId": google_query_id,
                "query": query,
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
        doc["_id"] = str(doc["_id"])
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

