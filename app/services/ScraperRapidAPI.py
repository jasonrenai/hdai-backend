"""
Service for scraping URLs via RapidAPI AI Content Scraper and extracting
Speaking Opportunities via LLM. Replaces crawl+scrape flow with single-URL scraping.
"""
from datetime import datetime
from typing import Optional
from bson import ObjectId
from urllib.parse import urlparse

from app.models.Scraper import ScraperModel
from app.helpers.RapidAPIScraper import RapidAPIScraper
from app.helpers.SpeakingOpportunityExtractor import SpeakingOpportunityExtractor
from app.helpers.OpportunityQualifier import qualify_opportunities_batch
from app.helpers.OpportunitySubmissionResolver import OpportunitySubmissionResolver


class ScraperRapidAPIService:
    """
    Uses RapidAPI AI Content Scraper to scrape a single URL, then extracts
    speaking opportunities via LLM. Crawler and legacy Scraper are not used.
    """

    def __init__(self):
        self.model = ScraperModel()
        self.rapidapi_scraper = RapidAPIScraper()
        self.opportunity_extractor = SpeakingOpportunityExtractor()
        self.submission_resolver = OpportunitySubmissionResolver(rapidapi_scraper=self.rapidapi_scraper)

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
        Background task: scrape URL via RapidAPI, extract opportunities via LLM, update DB.
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

            # 1. Scrape via RapidAPI AI Content Scraper
            result = self.rapidapi_scraper.scrape(url)
            if not result.get("success"):
                await self.model.update_by_id(
                    job_id,
                    {"status": "FAILED", "error": result.get("error", "Scraping failed")},
                )
                return

            content = result.get("data", {}).get("content", "")
            if not content:
                await self.model.update_by_id(
                    job_id,
                    {"status": "FAILED", "error": "No content returned from scraper"},
                )
                return

            # Optionally store name/description from scraper response
            name = result.get("data", {}).get("name")
            description = result.get("data", {}).get("description")
            update_payload = {}
            if name:
                update_payload["scrapedName"] = name
            if description:
                update_payload["scrapedDescription"] = description

            # 2. LLM extract speaking opportunities
            opportunities, llm_error = self.opportunity_extractor.extract(content, url=url)
            if opportunities:
                opportunities = self.submission_resolver.resolve_opportunities(
                    opportunities,
                    source_url=url,
                    source_page_content=content,
                    source_page_links=result.get("data", {}).get("urls") or [],
                )
                qualify_opportunities_batch(
                    opportunities,
                    scraper=self.rapidapi_scraper,
                    source_page_url=url,
                    source_page_content=content,
                )
            error_to_store = llm_error if llm_error and not opportunities else None

            # 3. Update DB with success
            await self.model.update_by_id(
                job_id,
                {
                    "status": "SUCCESS",
                    "opportunities": opportunities,
                    "error": error_to_store,
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
