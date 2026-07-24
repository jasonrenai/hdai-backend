"""
Controller for URL scraping via RapidAPI.
Creates entries in Scrapers collection (name, url, status=pending).
Scraping is processed by the weekly pending Scrapers cron (not in this request).
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from app.schemas.Opportunity import UrlScrapeCreateSchema
from app.schemas.ServerResponse import ServerResponse
from app.helpers.Utilities import Utils
from app.dependencies import get_url_scraper_rapidapi_service
from app.middleware.JWTVerification import jwt_validator
from app.services.UrlScraperRapidAPI import is_pdf_url

router = APIRouter(prefix="/api/v1/url-scraper", tags=["URL Scraper (RapidAPI)"])


@router.post("/", response_model=ServerResponse, status_code=201)
async def create_url_scrape(
    data: UrlScrapeCreateSchema,
    service=Depends(get_url_scraper_rapidapi_service),
    jwt_payload: dict = Depends(jwt_validator),
):
    """
    Submit a URL for scraping.
    Saves name + url + status=pending + createdAt to Scrapers.
    Processing runs on the weekly pending Scrapers cron (same pattern as Google queries).
    """
    try:
        url = (data.url or "").strip()
        name = (data.name or "").strip()
        if not url:
            raise HTTPException(
                status_code=400,
                detail={"data": None, "error": "URL is required", "success": False},
            )
        if not name:
            raise HTTPException(
                status_code=400,
                detail={"data": None, "error": "name is required", "success": False},
            )
        if is_pdf_url(url):
            raise HTTPException(
                status_code=400,
                detail={"data": None, "error": "PDF URLs are not scraped", "success": False},
            )

        user_id = jwt_payload.get("id")
        scraper_id = await service.create_scraper_job(url, name=name, user_id=user_id)

        return Utils.create_response(
            {
                "scraperId": scraper_id,
                "name": name,
                "url": url,
                "status": "pending",
                "message": "URL submitted. It will be processed by the weekly scraper cron.",
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


@router.get("/get-all-scrapers", response_model=ServerResponse)
async def get_all_scrapers(
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    limit: int = Query(10, ge=1, le=500, description="Items per page"),
    service=Depends(get_url_scraper_rapidapi_service),
    _jwt_payload: dict = Depends(jwt_validator),
):
    """List Scrapers entries (id, name, url, status, createdAt) with page/limit pagination."""
    try:
        data = await service.get_scrapers_list(page=page, limit=limit)
        return Utils.create_response(data, True)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail={"data": None, "error": str(e), "success": False},
        )


@router.delete("/delete-scraper/{scraper_id}", response_model=ServerResponse)
async def delete_scraper(
    scraper_id: str,
    service=Depends(get_url_scraper_rapidapi_service),
    _jwt_payload: dict = Depends(jwt_validator),
):
    """Delete a Scrapers URL entry by id."""
    try:
        deleted = await service.delete_scraper_job(scraper_id)
        if not deleted:
            raise HTTPException(
                status_code=404,
                detail={"data": None, "error": "Scraper not found", "success": False},
            )
        return Utils.create_response(
            {"scraperId": scraper_id, "message": "Scraper deleted successfully"},
            True,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail={"data": None, "error": str(e), "success": False},
        )


@router.get("/{scraper_id}", response_model=ServerResponse)
async def get_url_scraper_job(
    scraper_id: str,
    service=Depends(get_url_scraper_rapidapi_service),
    jwt_payload: dict = Depends(jwt_validator),
):
    """Get a Scrapers entry created by the URL scraper API."""
    try:
        doc = await service.get_scraper_job_by_id(scraper_id)
        if not doc:
            raise HTTPException(
                status_code=404,
                detail={"data": None, "error": "Scraper not found", "success": False},
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
