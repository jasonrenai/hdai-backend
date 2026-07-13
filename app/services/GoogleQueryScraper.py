import asyncio
import logging
from datetime import datetime
from typing import Optional

from app.config.recent_activity import (
    MESSAGE_GOOGLE_QUERIES_ADDED,
    RECENT_ACTIVITY_TYPE_GOOGLE_QUERIES,
    RECENT_ACTIVITY_TYPE_OPPORTUNITIES,
    message_opportunities_added,
)
from app.helpers.SerpHelper import SerpHelper
from app.models.GoogleQuery import GoogleQueryModel
from app.models.RecentActivity import RecentActivityModel
from app.services.UrlScraperRapidAPI import UrlScraperRapidAPIService, RAPIDAPI_DELAY_SECONDS, is_pdf_url

logger = logging.getLogger(__name__)

GOOGLE_QUERY_TOP_N = 5


class GoogleQueryScraperService:
    """
    Flow: Save query+createdAt+status=pending to GoogleQueries -> background task
    runs SERP -> takes top N URLs -> runs same RapidAPI+LLM extraction flow as UrlScraperRapidAPIService.
    """

    def __init__(self):
        self.google_query_model = GoogleQueryModel()
        self.url_scraper_service = UrlScraperRapidAPIService()
        self.recent_activity_model = RecentActivityModel()

    async def create_google_query_job(self, query: str, user_id: Optional[str] = None) -> str:
        doc = {
            "query": query,
            "status": "pending",
            "createdAt": datetime.utcnow(),
            "updatedAt": datetime.utcnow(),
            "urls": [],
            "urlCollectionIds": [],
            "error": None,
        }
        if user_id:
            doc["userId"] = user_id
        inserted_id = await self.google_query_model.create(doc)
        logger.info("GoogleQuery created google_query_id=%s query=%s", inserted_id, query[:120])
        return inserted_id

    async def get_google_query_by_id(self, google_query_id: str, user_id: Optional[str] = None):
        return await self.google_query_model.get_by_id(google_query_id, user_id=user_id)

    async def delete_google_query(self, google_query_id: str, user_id: Optional[str] = None) -> bool:
        """Delete a GoogleQuery by id. When user_id is set, only that user's record can be deleted."""

        return await self.google_query_model.delete_by_id(google_query_id, user_id=user_id)

    async def get_list(self, user_id: Optional[str] = None, skip: int = 0, limit: int = 100) -> dict:
        """List GoogleQueries with pagination. Filter by user_id when provided."""
        items = await self.google_query_model.get_list(user_id=user_id, skip=skip, limit=limit)
        total = await self.google_query_model.count(user_id=user_id)
        # Serialize _id for JSON
        for doc in items:
            doc["_id"] = str(doc["_id"])
        return {"googleQueries": items, "total": total}

    async def run_query_serp_and_scrape(self, google_query_id: str, query: str, user_id: Optional[str] = None) -> None:
        logger.info("GoogleQuery background job started google_query_id=%s query=%s", google_query_id, query[:120])
        await self.google_query_model.update_by_id(
            google_query_id,
            {"status": "running", "updatedAt": datetime.utcnow(), "error": None},
        )
        try:
            urls = await asyncio.to_thread(SerpHelper().search, query)
            non_pdf = [u for u in (urls or []) if not is_pdf_url(u)]
            top_urls = non_pdf[:GOOGLE_QUERY_TOP_N]
            await self.google_query_model.update_by_id(
                google_query_id,
                {"urls": top_urls, "updatedAt": datetime.utcnow()},
            )

            if not top_urls:
                await self.google_query_model.update_by_id(
                    google_query_id,
                    {"status": "completed", "updatedAt": datetime.utcnow()},
                )
                await self.recent_activity_model.try_insert_activity(
                    RECENT_ACTIVITY_TYPE_GOOGLE_QUERIES,
                    MESSAGE_GOOGLE_QUERIES_ADDED,
                )
                logger.info("GoogleQuery job completed (0 urls) google_query_id=%s", google_query_id)
                return

            url_collection_ids: list[str] = []
            total_opportunities_inserted = 0
            for i, url in enumerate(top_urls):
                try:
                    if i > 0:
                        await asyncio.sleep(RAPIDAPI_DELAY_SECONDS)
                    url_collection_id = await self.url_scraper_service.create_url_scrape_job(url, user_id=user_id)
                    url_collection_ids.append(url_collection_id)
                    await self.google_query_model.update_by_id(
                        google_query_id,
                        {"urlCollectionIds": url_collection_ids, "updatedAt": datetime.utcnow()},
                    )
                    n = await self.url_scraper_service.run_scrape_and_extract(
                        url_collection_id,
                        url,
                        delay_seconds=RAPIDAPI_DELAY_SECONDS,
                        from_google_query=True,
                        google_search_query=query,
                    )
                    total_opportunities_inserted += n
                    logger.info(
                        "[opp-pipeline] google_query_id=%s url_index=%d/%d inserted=%d url=%s url_collection_id=%s",
                        google_query_id,
                        i + 1,
                        len(top_urls),
                        n,
                        url[:120],
                        url_collection_id,
                    )
                except Exception as e:
                    logger.exception("GoogleQuery job url failed google_query_id=%s url=%s err=%s", google_query_id, url[:120], e)

            await self.google_query_model.update_by_id(
                google_query_id,
                {"status": "completed", "updatedAt": datetime.utcnow()},
            )
            await self.recent_activity_model.try_insert_activity(
                RECENT_ACTIVITY_TYPE_GOOGLE_QUERIES,
                MESSAGE_GOOGLE_QUERIES_ADDED,
            )
            if total_opportunities_inserted > 0:
                await self.recent_activity_model.try_insert_activity(
                    RECENT_ACTIVITY_TYPE_OPPORTUNITIES,
                    message_opportunities_added(total_opportunities_inserted),
                )
            logger.info(
                "[opp-pipeline] GoogleQuery job completed google_query_id=%s urls=%d total_opportunities_inserted=%d",
                google_query_id,
                len(top_urls),
                total_opportunities_inserted,
            )
        except Exception as e:
            logger.exception("GoogleQuery job failed google_query_id=%s err=%s", google_query_id, e)
            await self.google_query_model.update_by_id(
                google_query_id,
                {"status": "failed", "error": str(e), "updatedAt": datetime.utcnow()},
            )

    async def process_all_pending(self) -> dict:
        """
        Claim and process every pending GoogleQuery (no limit). Used by the 72h scheduled cron.
        Same pipeline as process_pending_batch: SERP -> top URLs -> scrape -> opportunities.
        """
        return await self._process_claimed_pending(
            await self.google_query_model.claim_all_pending_jobs()
        )

    async def process_pending_batch(self, limit: int = 25) -> dict:
        """
        Claim up to `limit` pending GoogleQueries (e.g. manual script runs) and run the same pipeline as the API
        background task: SERP -> top URLs -> RapidAPI scrape -> opportunities (Mongo + vector) with duplicate checks.
        Jobs run sequentially within this batch to reduce SERP/RapidAPI rate pressure.
        """
        return await self._process_claimed_pending(
            await self.google_query_model.claim_pending_jobs(limit=limit)
        )

    async def _process_claimed_pending(self, claimed: list) -> dict:
        summary: dict = {
            "claimed": len(claimed),
            "completed": 0,
            "failed": 0,
            "skipped_invalid": 0,
            "unexpected_status_after_run": 0,
        }
        for doc in claimed:
            google_query_id = str(doc["_id"])
            query = (doc.get("query") or "").strip()
            user_id = doc.get("userId")
            if user_id is not None:
                user_id = str(user_id)
            if not query:
                await self.google_query_model.update_by_id(
                    google_query_id,
                    {
                        "status": "failed",
                        "error": "missing or empty query",
                        "updatedAt": datetime.utcnow(),
                    },
                )
                summary["skipped_invalid"] += 1
                continue
            await self.run_query_serp_and_scrape(google_query_id, query, user_id)
            final = await self.google_query_model.get_by_id(google_query_id)
            st = (final or {}).get("status")
            if st == "completed":
                summary["completed"] += 1
            elif st == "failed":
                summary["failed"] += 1
            else:
                summary["unexpected_status_after_run"] += 1
                logger.warning(
                    "GoogleQuery finished with unexpected status=%s google_query_id=%s",
                    st,
                    google_query_id,
                )
        return summary

