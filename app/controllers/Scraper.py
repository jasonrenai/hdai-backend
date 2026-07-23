from fastapi import APIRouter, Depends, HTTPException, Query
from app.schemas.Scraper import ScraperCreateSchema, ScraperUpdateSchema
from app.schemas.ServerResponse import ServerResponse
from app.helpers.Utilities import Utils
from app.middleware.JWTVerification import jwt_validator
from app.dependencies import get_scraper_service, get_url_scraper_rapidapi_service

router = APIRouter(prefix="/api/v1/scrapers", tags=["Scrapers"])


@router.post("/create-scraper", response_model=ServerResponse, status_code=201)
async def create_scraper(
    data: ScraperCreateSchema,
    service=Depends(get_scraper_service),
    jwt_payload: dict = Depends(jwt_validator),
):
    try:
        user_id = jwt_payload["id"]
        result = await service.create(user_id, data)
        if not result["success"]:
            raise HTTPException(
                status_code=400,
                detail={"data": None, "error": result["error"], "success": False},
            )
        return Utils.create_response(result["data"], True)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail={"data": None, "error": str(e), "success": False},
        )


@router.get("/get-all-scrapers", response_model=ServerResponse)
async def list_scrapers(
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    limit: int = Query(10, ge=1, le=500, description="Items per page"),
    jwt_payload: dict = Depends(jwt_validator),
    service=Depends(get_url_scraper_rapidapi_service),
):
    """List all scrapers from UrlCollection with page/limit pagination."""
    try:
        result = await service.get_list(page=page, limit=limit)
        if not result["success"]:
            raise HTTPException(
                status_code=400,
                detail={"data": None, "error": result["error"], "success": False},
            )
        return Utils.create_response(result["data"], True)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail={"data": None, "error": str(e), "success": False},
        )


@router.get("/get-scraper/{scraper_id}", response_model=ServerResponse)
async def get_scraper(
    scraper_id: str,
    jwt_payload: dict = Depends(jwt_validator),
    service=Depends(get_url_scraper_rapidapi_service),
):
    """Get a single scraper from UrlCollection by ID."""
    try:
        user_id = jwt_payload["id"]
        result = await service.get_by_id(scraper_id, user_id)
        if not result["success"]:
            raise HTTPException(
                status_code=404 if result["error"] == "Scraper not found" else 400,
                detail={"data": None, "error": result["error"], "success": False},
            )
        return Utils.create_response(result["data"], True)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail={"data": None, "error": str(e), "success": False},
        )


@router.put("/{scraper_id}", response_model=ServerResponse)
async def update_scraper(
    scraper_id: str,
    data: ScraperUpdateSchema,
    service=Depends(get_scraper_service),
    jwt_payload: dict = Depends(jwt_validator),
):
    try:
        user_id = jwt_payload["id"]
        result = await service.update(scraper_id, user_id, data)
        if not result["success"]:
            raise HTTPException(
                status_code=404 if result["error"] == "Scraper not found" else 400,
                detail={"data": None, "error": result["error"], "success": False},
            )
        return Utils.create_response(result["data"], True)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail={"data": None, "error": str(e), "success": False},
        )


@router.delete("/{scraper_id}", response_model=ServerResponse)
async def delete_scraper(
    scraper_id: str,
    jwt_payload: dict = Depends(jwt_validator),
    service=Depends(get_url_scraper_rapidapi_service),
):
    """Delete a scraper (UrlCollection entry) by ID. Deletion is by scraper_id only."""
    try:
        result = await service.delete(scraper_id)
        if not result["success"]:
            raise HTTPException(
                status_code=404 if result["error"] == "Scraper not found" else 400,
                detail={"data": None, "error": result["error"], "success": False},
            )
        return Utils.create_response(result["data"], True)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail={"data": None, "error": str(e), "success": False},
        )