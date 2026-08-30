import asyncio
import logging
import os
from datetime import datetime
from typing import Optional

from app.config.recent_activity import (
    MESSAGE_GOOGLE_QUERIES_ADDED,
    RECENT_ACTIVITY_TYPE_GOOGLE_QUERIES,
    RECENT_ACTIVITY_TYPE_OPPORTUNITIES,
    message_opportunities_added,
)
from app.helpers.GoogleQueryTopicTagger import resolve_related_topics
from app.helpers.SerpHelper import SerpHelper
from app.models.GoogleQuery import GoogleQueryModel
from app.models.Opportunity import OpportunityModel
from app.models.RecentActivity import RecentActivityModel
from app.services.UrlScraperRapidAPI import UrlScraperRapidAPIService, RAPIDAPI_DELAY_SECONDS, is_pdf_url

logger = logging.getLogger(__name__)

GOOGLE_QUERY_TOP_N = 30
SERP_PAGE_SIZE = 10


class GoogleQueryScraperService:
    """
    Flow: Save query+createdAt+status=pending to GoogleQueries -> background task
    runs SERP (3 pages / top N URLs) -> skips URLs already known in Opportunities ->
    runs RapidAPI+LLM extraction flow as UrlScraperRapidAPIService.
    """

    def __init__(self):
        self.google_query_model = GoogleQueryModel()
        self.url_scraper_service = UrlScraperRapidAPIService()
        self.recent_activity_model = RecentActivityModel()
        self.opportunity_model = OpportunityModel()

    async def create_google_query_job(self, query: str, user_id: Optional[str] = None) -> str:
        related_topics = await asyncio.to_thread(resolve_related_topics, query)
        doc = {
            "query": query,
            "status": "pending",
            "createdAt": datetime.utcnow(),
            "updatedAt": datetime.utcnow(),
            "urls": [],
            "urlCollectionIds": [],
            "error": None,
            "relatedTopics": related_topics,
        }
        if user_id:
            doc["userId"] = user_id
        inserted_id = await self.google_query_model.create(doc)
        logger.info(
            "GoogleQuery created google_query_id=%s query=%s relatedTopics=%s",
            inserted_id,
            query[:120],
            related_topics,
        )
        return inserted_id

    async def _ensure_related_topics(self, google_query_id: str, query: str) -> list[str]:
        """Tag relatedTopics if missing/empty (e.g. older or script-inserted docs)."""
        existing = await self.google_query_model.get_by_id(google_query_id)
        current = (existing or {}).get("relatedTopics")
        if isinstance(current, list) and len(current) > 0:
            return current
        related_topics = await asyncio.to_thread(resolve_related_topics, query)
        await self.google_query_model.set_related_topics(google_query_id, related_topics)
        logger.info(
            "GoogleQuery relatedTopics set google_query_id=%s relatedTopics=%s",
            google_query_id,
            related_topics,
        )
        return related_topics

    async def get_google_query_by_id(self, google_query_id: str, user_id: Optional[str] = None):
        return await self.google_query_model.get_by_id(google_query_id, user_id=user_id)

    async def delete_google_query(self, google_query_id: str, user_id: Optional[str] = None) -> bool:
        """Delete a GoogleQuery by id. When user_id is set, only that user's record can be deleted."""

        return await self.google_query_model.delete_by_id(google_query_id, user_id=user_id)

    @staticmethod
    def _normalize_related_topics_param(related_topics: Optional[list] = None) -> list[str]:
        """Flatten repeated/comma-separated relatedTopics query params."""
        if not related_topics:
            return []
        out: list[str] = []
        seen = set()
        for raw in related_topics:
            for part in str(raw or "").split(","):
                topic = part.strip()
                if not topic:
                    continue
                key = topic.casefold()
                if key in seen:
                    continue
                seen.add(key)
                out.append(topic)
        return out

    async def get_list(
        self,
        user_id: Optional[str] = None,
        page: int = 1,
        limit: int = 10,
        search: Optional[str] = None,
        related_topics: Optional[list] = None,
    ) -> dict:
        """List GoogleQueries with page/limit pagination and optional search filters."""
        page = max(1, int(page or 1))
        limit = max(1, int(limit or 10))
        skip = (page - 1) * limit
        topics = self._normalize_related_topics_param(related_topics)
        search_text = (search or "").strip() or None
        items = await self.google_query_model.get_list(
            user_id=user_id,
            skip=skip,
            limit=limit,
            search=search_text,
            related_topics=topics or None,
        )
        total = await self.google_query_model.count(
            user_id=user_id,
            search=search_text,
            related_topics=topics or None,
        )
        for doc in items:
            doc["_id"] = str(doc["_id"])
            if not isinstance(doc.get("relatedTopics"), list):
                doc["relatedTopics"] = []
        return {
            "googleQueries": items,
            "total": total,
            "page": page,
            "limit": limit,
            "totalPages": (total + limit - 1) // limit if limit > 0 else 0,
        }

    async def run_query_serp_and_scrape(self, google_query_id: str, query: str, user_id: Optional[str] = None) -> None:
        logger.info("GoogleQuery background job started google_query_id=%s query=%s", google_query_id, query[:120])
        await self.google_query_model.update_by_id(
            google_query_id,
            {"status": "running", "updatedAt": datetime.utcnow(), "error": None},
        )
        try:
            await self._ensure_related_topics(google_query_id, query)
            urls = await asyncio.to_thread(
                SerpHelper().search_multi_page,
                query,
                GOOGLE_QUERY_TOP_N,
                SERP_PAGE_SIZE,
            )
            non_pdf = [u for u in (urls or []) if not is_pdf_url(u)]
            top_urls = non_pdf[:GOOGLE_QUERY_TOP_N]

            # Pre-scrape dedupe: skip SERP URLs already stored as opportunity link or source_url
            already_known = await self.opportunity_model.find_urls_already_known(top_urls)
            urls_to_scrape = [u for u in top_urls if u not in already_known]
            skipped = [u for u in top_urls if u in already_known]
            if skipped:
                logger.info(
                    "[opp-pipeline] google_query_id=%s pre_scrape_skip=%d already_known_urls (of %d serp)",
                    google_query_id,
                    len(skipped),
                    len(top_urls),
                )
                for u in skipped:
                    logger.info(
                        "[opp-pipeline] google_query_id=%s skip_existing_url=%s",
                        google_query_id,
                        u[:120],
                    )

            await self.google_query_model.update_by_id(
                google_query_id,
                {
                    "urls": top_urls,
                    "urlsSkippedExisting": skipped,
                    "updatedAt": datetime.utcnow(),
                },
            )

            if not urls_to_scrape:
                await self.google_query_model.update_by_id(
                    google_query_id,
                    {"status": "completed", "updatedAt": datetime.utcnow()},
                )
                await self.recent_activity_model.try_insert_activity(
                    RECENT_ACTIVITY_TYPE_GOOGLE_QUERIES,
                    MESSAGE_GOOGLE_QUERIES_ADDED,
                )
                logger.info(
                    "GoogleQuery job completed (0 urls to scrape after dedupe) google_query_id=%s serp=%d skipped=%d",
                    google_query_id,
                    len(top_urls),
                    len(skipped),
                )
                return

            url_collection_ids: list[str] = []
            total_opportunities_inserted = 0
            for i, url in enumerate(urls_to_scrape):
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
                        len(urls_to_scrape),
                        n,
                        url[:120],
                        url_collection_id,
                    )
                except Exception as e:
                    logger.exception(
                        "GoogleQuery job url failed google_query_id=%s url=%s err=%s",
                        google_query_id,
                        url[:120],
                        e,
                    )

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
                "[opp-pipeline] GoogleQuery job completed google_query_id=%s serp=%d scraped=%d skipped_existing=%d total_opportunities_inserted=%d",
                google_query_id,
                len(top_urls),
                len(urls_to_scrape),
                len(skipped),
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
        Drain all pending GoogleQueries one at a time. Used by the weekly scheduled cron.

        Claims a single job (pending -> running), scrapes it to completion, then claims the
        next — so only one document is ``running`` at a time. Aborting mid-drain leaves the
        rest still ``pending``.

        At start, reclaims stale ``running`` jobs left behind by a killed/redeployed worker.
        Mongo blips retry with backoff instead of aborting the whole drain.
        """
        from pymongo.errors import AutoReconnect, NetworkTimeout, ServerSelectionTimeoutError

        summary: dict = {
            "claimed": 0,
            "completed": 0,
            "failed": 0,
            "skipped_invalid": 0,
            "unexpected_status_after_run": 0,
            "reclaimed_stale": 0,
            "mongo_retries": 0,
        }

        try:
            stale_raw = (os.getenv("GOOGLE_QUERY_STALE_RUNNING_MINUTES") or "120").strip()
            stale_minutes = max(1, int(stale_raw))
        except ValueError:
            stale_minutes = 120

        try:
            reclaimed = await self.google_query_model.reclaim_stale_running_jobs(stale_minutes)
            summary["reclaimed_stale"] = reclaimed
            if reclaimed:
                logger.info(
                    "Reclaimed %s stale running GoogleQueries (>%s min) back to pending",
                    reclaimed,
                    stale_minutes,
                )
        except Exception:
            logger.exception("Failed to reclaim stale running GoogleQueries; continuing drain")

        consecutive_mongo_failures = 0
        while True:
            try:
                claimed = await self.google_query_model.claim_pending_jobs(limit=1)
                consecutive_mongo_failures = 0
            except (ServerSelectionTimeoutError, AutoReconnect, NetworkTimeout) as e:
                summary["mongo_retries"] += 1
                consecutive_mongo_failures += 1
                sleep_s = min(300, 30 * consecutive_mongo_failures)
                logger.warning(
                    "Mongo blip claiming GoogleQuery (attempt %s): %s; sleep %ss",
                    consecutive_mongo_failures,
                    e,
                    sleep_s,
                )
                if consecutive_mongo_failures >= 10:
                    logger.error("Too many consecutive Mongo claim failures; stopping drain")
                    break
                await asyncio.sleep(sleep_s)
                continue

            if not claimed:
                break

            try:
                batch = await self._process_claimed_pending(claimed)
                consecutive_mongo_failures = 0
            except (ServerSelectionTimeoutError, AutoReconnect, NetworkTimeout) as e:
                summary["mongo_retries"] += 1
                consecutive_mongo_failures += 1
                # Active claim may be stuck running — reclaim stale (and this one if old enough later)
                # Immediately put this claimed id back to pending so sequential drain can retry.
                for doc in claimed:
                    try:
                        await self.google_query_model.update_by_id(
                            str(doc["_id"]),
                            {
                                "status": "pending",
                                "error": None,
                                "updatedAt": datetime.utcnow(),
                            },
                        )
                    except Exception:
                        logger.exception(
                            "Failed to reset GoogleQuery to pending after Mongo blip id=%s",
                            doc.get("_id"),
                        )
                sleep_s = min(300, 30 * consecutive_mongo_failures)
                logger.warning(
                    "Mongo blip processing GoogleQuery (attempt %s): %s; reset claimed->pending, sleep %ss",
                    consecutive_mongo_failures,
                    e,
                    sleep_s,
                )
                if consecutive_mongo_failures >= 10:
                    logger.error("Too many consecutive Mongo process failures; stopping drain")
                    break
                await asyncio.sleep(sleep_s)
                continue

            for k in (
                "claimed",
                "completed",
                "failed",
                "skipped_invalid",
                "unexpected_status_after_run",
            ):
                summary[k] += batch.get(k, 0)
        return summary

    async def process_pending_batch(self, limit: int = 25) -> dict:
        """
        Claim up to `limit` pending GoogleQueries (e.g. manual script runs) and run the same pipeline as the API
        background task: SERP -> top URLs -> RapidAPI scrape -> opportunities (Mongo + vector) with duplicate checks.

        Claims one job at a time so a killed worker does not leave the rest of the batch stuck as ``running``.
        """
        summary: dict = {
            "claimed": 0,
            "completed": 0,
            "failed": 0,
            "skipped_invalid": 0,
            "unexpected_status_after_run": 0,
        }
        limit = max(0, int(limit))
        for _ in range(limit):
            claimed = await self.google_query_model.claim_pending_jobs(limit=1)
            if not claimed:
                break
            batch = await self._process_claimed_pending(claimed)
            for k in summary:
                summary[k] += batch.get(k, 0)
        return summary

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

