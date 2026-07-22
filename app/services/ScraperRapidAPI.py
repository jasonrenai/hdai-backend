"""
Service for scraping URLs via RapidAPI AI Content Scraper and extracting
Speaking Opportunities via LLM. Replaces crawl+scrape flow with single-URL scraping.
Uses the shared multi-hop OpportunityDiscoveryPipeline.
"""
from datetime import datetime
from typing import Optional
from bson import ObjectId
from urllib.parse import urlparse

from app.models.Scraper import ScraperModel
from app.helpers.OpportunityDiscoveryPipeline import OpportunityDiscoveryPipeline, is_pdf_url


class ScraperRapidAPIService:
    """
    Uses RapidAPI AI Content Scraper to scrape a single URL, then extracts
    speaking opportunities via the multi-hop discovery pipeline.
    """

    def __init__(self):
        self.model = ScraperModel()

    async def create_scrape_job(self, url: str, user_id: str) -> str:
        """
        Create a pending scrape job and return its ID.
        """
        parsed = urlparse(url)
        source_name = parsed.netloc or parsed.path or "unknown"

        doc = {
            "sourceName": source_name,
            "url": url,
            "description": None,
            "userId": user_id,
            "opportunities": [],
            "status": "PENDING_SCRAPING",
            "error": None,
            "createdAt": datetime.utcnow(),
            "updatedAt": None,
        }
        inserted_id = await self.model.create(doc)
        return inserted_id

    async def get_by_id(self, scraper_id: str, user_id: Optional[str] = None) -> Optional[dict]:
        """Get a scrape job by ID."""
        doc = await self.model.get_by_id(scraper_id, user_id)
        if doc:
            return doc.model_dump(by_alias=True, exclude_none=True)
        return None

    async def run_scrape_and_extract(self, job_id: str) -> None:
        """
        Background task: multi-hop scrape/discover opportunities, update DB.
        """
        try:
            await self.model.update_by_id(job_id, {"status": "IN_PROGRESS", "error": None})

            doc = await self.model.collection.find_one({"_id": ObjectId(job_id)})
            if not doc:
                await self.model.update_by_id(
                    job_id,
                    {"status": "FAILED", "error": "Job not found"},
                )
                return

            url = doc.get("url")
            if is_pdf_url(url or ""):
                await self.model.update_by_id(
                    job_id,
                    {"status": "FAILED", "error": "PDF URLs are not scraped"},
                )
                return

            parsed = OpportunityDiscoveryPipeline().run(url)
            if parsed is None:
                await self.model.update_by_id(
                    job_id,
                    {"status": "FAILED", "error": "Scraping or discovery failed"},
                )
                return

            opportunities = parsed.get("opportunities") or []
            update_payload = {}
            if parsed.get("source_name"):
                update_payload["scrapedName"] = parsed["source_name"]
            if parsed.get("description"):
                update_payload["scrapedDescription"] = parsed["description"]

            await self.model.update_by_id(
                job_id,
                {
                    "status": "SUCCESS",
                    "opportunities": opportunities,
                    "error": None,
                    "scrapedUrlCount": 1,
                    **update_payload,
                },
            )
        except Exception as e:
            err_msg = str(e)
            try:
                await self.model.update_by_id(
                    job_id,
                    {"status": "FAILED", "error": err_msg},
                )
            except Exception:
                pass
            print(f"[ScraperRapidAPIService] Job {job_id} failed: {err_msg}")
