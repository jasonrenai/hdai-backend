"""
Service for scraping URLs via RapidAPI and storing opportunities.
Flow: Save url+createdAt to UrlCollection -> background task runs multi-hop discovery
(classify page → resolve official opportunity URLs from blogs → verify speaking → exact dates →
submission resolve) -> inserts verified opportunities into Opportunities collection.
No connection with existing Scraper/Scrapers collection.
Blocking I/O (RapidAPI requests, OpenAI) runs in a thread pool to avoid blocking the event loop.
PDF URLs are not scraped. Only opportunities with all required fields (link, event_name, location, topics, start_date, end_date, speaking_format, delivery_mode, target_audiences) are saved.
Dead/wrong opportunity links and pages the LLM rejects as non-speaking for this event are dropped before save.
Qualified opportunities (isQualified) are upserted to Pinecone; unqualified are Mongo-only with reasonForUnqualify.
"""
import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.config.recent_activity import (
    MESSAGE_SCRAPER_ADDED,
    RECENT_ACTIVITY_TYPE_OPPORTUNITIES,
    RECENT_ACTIVITY_TYPE_SCRAPER,
    message_opportunities_added,
)
from app.models.UrlCollection import UrlCollectionModel
from app.models.Scraper import ScraperModel
from app.models.Opportunity import OpportunityModel, opportunity_dedupe_key
from app.models.RecentActivity import RecentActivityModel
from app.helpers.OpportunityDiscoveryPipeline import OpportunityDiscoveryPipeline, is_pdf_url
from app.helpers.PineconeOpportunityStore import PineconeOpportunityStore
from app.helpers.SerpHelper import SerpHelper
from app.helpers.SpeakingOpportunityExtractor import _is_future_or_today, _parse_date_to_iso
from app.helpers.TargetAudienceCatalog import load_target_audience_names_from_db
from app.agents.EventDetailEnricherAgent import EventDetailEnricherAgent

RAPIDAPI_DELAY_SECONDS = 5
TEDX_CRON_QUERY = "Ted X opportunities"
TEDX_CRON_TOP_N = 5

logger = logging.getLogger(__name__)

DESCRIPTION_MAX_LENGTH = 500
# UrlCollection / Scrapers require a non-empty description; use this when scrape returns none
DESCRIPTION_FALLBACK = "Scraped page"

# Job tracking collection for scrape status updates
JOB_STORE_URL_COLLECTION = "url_collection"
JOB_STORE_SCRAPERS = "scrapers"


def filter_complete_opportunities(opportunities: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Keep only opportunities that have all required fields filled. Canonical topics may be empty when
    the content does not match the allowed topic catalog, but aipredictedTopics must then be present.
    Also requires start_date and end_date to be today or in the future.
    """
    result = []
    for opp in opportunities:
        link = (opp.get("link") or opp.get("url") or "").strip()
        event_name = (opp.get("event_name") or opp.get("title") or "").strip()
        location = (opp.get("location") or "").strip()
        topics = opp.get("topics")
        ai_predicted_topics = opp.get("aipredictedTopics")
        start_date = opp.get("start_date")
        end_date = opp.get("end_date")
        speaking_format = (opp.get("speaking_format") or "").strip()
        delivery_mode = (opp.get("delivery_mode") or "").strip()
        target_audiences = opp.get("target_audiences")

        if not link or not event_name or not location:
            continue
        has_topics = isinstance(topics, list) and len(topics) > 0
        has_ai_predicted_topics = isinstance(ai_predicted_topics, list) and len(ai_predicted_topics) > 0
        if not has_topics and not has_ai_predicted_topics:
            continue
        if start_date is None or not str(start_date).strip():
            continue
        if end_date is None or not str(end_date).strip():
            continue
        start_iso = _parse_date_to_iso(start_date, require_day=True)
        end_iso = _parse_date_to_iso(end_date, require_day=True) or start_iso
        if not start_iso or not _is_future_or_today(start_iso) or not _is_future_or_today(end_iso):
            continue
        if not speaking_format or not delivery_mode:
            continue
        if not isinstance(target_audiences, list):
            continue
        result.append(opp)
    return result


def _sync_scrape_extract_enrich(
    url: str,
    delay_seconds: float = 0,
    target_audiences: Optional[list] = None,
) -> Optional[dict]:
    """
    Synchronous multi-hop discovery pipeline. Runs in thread pool to avoid blocking event loop.
    Returns dict with keys: source_name, description, opportunities; or None on failure.
    Does not scrape URLs that end with .pdf.
    """
    if is_pdf_url(url):
        return None
    pipeline = OpportunityDiscoveryPipeline(
        delay_seconds=delay_seconds,
        target_audiences=target_audiences,
    )
    return pipeline.run(url, delay_seconds=delay_seconds)


class UrlScraperRapidAPIService:
    """
    Scrapes URLs via RapidAPI AI Content Scraper, extracts speaking opportunities via LLM.
    - Direct API (/url-scraper): creates Scrapers entry (name, url, status=pending), then scrapes.
    - Google query / cron: creates UrlCollection entries, then scrapes.
    Inserts verified opportunities into Opportunities collection.
    """

    def __init__(self):
        self.url_collection_model = UrlCollectionModel()
        self.scraper_model = ScraperModel()
        self.opportunity_model = OpportunityModel()
        self.enricher_agent = EventDetailEnricherAgent()
        self.recent_activity_model = RecentActivityModel()

    async def _update_job_status(
        self,
        job_id: str,
        update_data: dict,
        *,
        job_store: str = JOB_STORE_URL_COLLECTION,
    ) -> None:
        if job_store == JOB_STORE_SCRAPERS:
            data = {k: v for k, v in update_data.items() if k != "updatedAt"}
            await self.scraper_model.update_by_id(job_id, data)
        else:
            await self.url_collection_model.update_by_id(job_id, update_data)

    async def create_scraper_job(
        self,
        url: str,
        name: str,
        user_id: str = None,
    ) -> str:
        """
        Create a Scrapers collection entry for the direct URL scraper API.
        Stores name, url, status=pending, createdAt (and userId when provided).
        """
        if is_pdf_url(url):
            raise ValueError("PDF URLs are not scraped")
        name_s = (name or "").strip()
        if not name_s:
            raise ValueError("name is required")
        doc = {
            "name": name_s,
            "sourceName": name_s,  # keep compatible with older Scrapers schema consumers
            "url": url,
            "status": "pending",
            "createdAt": datetime.utcnow(),
        }
        if user_id:
            doc["userId"] = user_id
        inserted_id = await self.scraper_model.create(doc)
        logger.info(
            "Scraper created scraper_id=%s name=%s url=%s",
            inserted_id,
            name_s[:80],
            url[:80],
        )
        return inserted_id

    async def create_url_scrape_job(self, url: str, user_id: str = None, topics: Optional[list] = None) -> str:
        """
        Save url and createdAt to UrlCollection (Google query / cron path).
        sourceName and description are updated after RapidAPI scrape.
        """
        if is_pdf_url(url):
            raise ValueError("PDF URLs are not scraped")
        doc = {
            "url": url,
            "status": "pending",
            "createdAt": datetime.utcnow(),
            "inputSource": "Google Query",
        }
        if user_id:
            doc["userId"] = user_id
        if topics is not None and len(topics) > 0:
            doc["topics"] = topics
        inserted_id = await self.url_collection_model.create(doc)
        logger.info("UrlCollection created url_collection_id=%s url=%s", inserted_id, url[:80])
        return inserted_id

    async def get_url_collection_by_id(self, url_collection_id: str, user_id: str = None):
        """Get a UrlCollection entry by ID."""
        return await self.url_collection_model.get_by_id(url_collection_id, user_id)

    async def get_scraper_job_by_id(self, scraper_id: str, user_id: str = None):
        """Get a Scrapers entry by ID (raw dict)."""
        return await self.scraper_model.get_doc_by_id(scraper_id, user_id)

    async def get_scrapers_list(self, page: int = 1, limit: int = 10) -> dict:
        """Paginated Scrapers list (id, name, url, status, createdAt only)."""
        page = max(1, int(page or 1))
        limit = max(1, int(limit or 10))
        skip = (page - 1) * limit
        items = await self.scraper_model.get_list_summary(skip=skip, limit=limit)
        total = await self.scraper_model.count_all()
        return {
            "scrapers": items,
            "total": total,
            "page": page,
            "limit": limit,
            "totalPages": (total + limit - 1) // limit if limit > 0 else 0,
        }

    async def delete_scraper_job(self, scraper_id: str) -> bool:
        """Delete a Scrapers entry by id."""
        return await self.scraper_model.delete_by_id(scraper_id)

    async def get_list(self, page: int = 1, limit: int = 10) -> dict:
        """Get list of UrlCollection entries (for get-all-scrapers) with page/limit pagination."""
        page = max(1, int(page or 1))
        limit = max(1, int(limit or 10))
        skip = (page - 1) * limit
        items = await self.url_collection_model.get_list(user_id=None, skip=skip, limit=limit)
        total = await self.url_collection_model.count(user_id=None)
        for doc in items:
            if doc.get("_id") is not None:
                doc["_id"] = str(doc["_id"])
        return {
            "success": True,
            "data": {
                "scrapers": items,
                "total": total,
                "page": page,
                "limit": limit,
                "totalPages": (total + limit - 1) // limit if limit > 0 else 0,
            },
        }

    async def get_by_id(self, url_collection_id: str, user_id: str) -> dict:
        """Get a single UrlCollection by ID (for get-scraper)."""
        doc = await self.url_collection_model.get_by_id(url_collection_id, user_id)
        if not doc:
            return {"success": False, "data": None, "error": "Scraper not found"}
        return {"success": True, "data": doc}

    async def delete(self, url_collection_id: str) -> dict:
        """Delete a UrlCollection entry by ID (for delete-scraper). Deletion is by scraper_id only."""
        deleted = await self.url_collection_model.delete_by_id(url_collection_id, user_id=None)
        if not deleted:
            return {"success": False, "data": None, "error": "Scraper not found"}
        return {"success": True, "data": "Scraper deleted successfully"}

    async def run_scrape_and_extract(
        self,
        job_id: str,
        url: str,
        delay_seconds: float = 0,
        from_google_query: bool = False,
        google_search_query: str = "",
        job_store: str = JOB_STORE_URL_COLLECTION,
    ) -> int:
        """
        Background task: scrape URL via RapidAPI, extract opportunities via LLM,
        insert each opportunity as root-level doc in Opportunities collection.
        Blocking I/O runs in thread pool so it does not block other requests.

        Returns:
            Number of opportunity documents inserted for this URL (0 if none or on failure).

        Args:
            job_id: UrlCollection or Scrapers document ID (depending on job_store)
            url: URL to scrape (also used as source_url on each opportunity)
            delay_seconds: Optional delay before each RapidAPI call (e.g. 5 for cron). Default 0.
            from_google_query: If True, opportunities are tagged as found via Google query search; if False, from direct URL scraping.
            Per-URL recent-activity for scraper/opportunities is skipped when True; the caller aggregates one opportunities row.
            google_search_query: When from_google_query is True, the SERP query string (stored on source and included in vector search text).
            job_store: "url_collection" (default) or "scrapers" — which collection to update status on.
        """
        url_collection_id = job_id  # keep log/metadata key name stable for UrlCollection path
        logger.info(
            "Background job started job_id=%s job_store=%s url=%s",
            job_id,
            job_store,
            url[:80],
        )
        try:
            if is_pdf_url(url):
                logger.info("Skipping PDF URL job_id=%s", job_id)
                await self._update_job_status(job_id, {"status": "failed"}, job_store=job_store)
                return 0
            # Load audience catalog from Mongo before sync thread work
            target_audiences = await load_target_audience_names_from_db()
            # Run blocking work (RapidAPI, OpenAI, enricher) in thread pool - prevents blocking event loop
            parsed = await asyncio.to_thread(
                _sync_scrape_extract_enrich,
                url,
                delay_seconds,
                target_audiences,
            )
            if parsed is None:
                logger.error("Job %s scrape/extract failed", job_id)
                await self._update_job_status(job_id, {"status": "failed"}, job_store=job_store)
                return 0

            source_name = parsed["source_name"]
            description = parsed["description"]
            opportunities = parsed["opportunities"]
            logger.info(
                "[opp-pipeline] job=%s from_google_query=%s after_sync opportunities=%d url=%s",
                job_id,
                from_google_query,
                len(opportunities or []),
                url[:120],
            )

            complete = filter_complete_opportunities(opportunities)
            dropped = len(opportunities) - len(complete)
            logger.info(
                "[opp-pipeline] job=%s complete_filter complete=%d incomplete_dropped=%d",
                job_id,
                len(complete),
                dropped,
            )
            if dropped:
                logger.info(
                    "[opp-pipeline] job=%s dropped %d opportunities missing required fields "
                    "(link, event_name, location, topics or aipredictedTopics, start_date, end_date, "
                    "speaking_format, delivery_mode, target_audiences)",
                    job_id,
                    dropped,
                )

            # Unique topics from saved opportunities
            extracted_topics = sorted(
                set(
                    str(t).strip()
                    for opp in complete
                    for t in (opp.get("topics") or [])
                    if t and str(t).strip()
                )
            )

            # Description is compulsory; use fallback if empty
            description_for_db = (description or "").strip() or DESCRIPTION_FALLBACK

            # Update tracking collection with scrape results
            status_update = {
                "sourceName": source_name,
                "description": description_for_db,
                "status": "completed",
                "topics": extracted_topics,
                "updatedAt": datetime.utcnow(),
            }
            await self._update_job_status(job_id, status_update, job_store=job_store)

            if not from_google_query:
                await self.recent_activity_model.try_insert_activity(
                    RECENT_ACTIVITY_TYPE_SCRAPER,
                    MESSAGE_SCRAPER_ADDED,
                )

            if not opportunities:
                logger.info(
                    "[opp-pipeline] job=%s completed inserted=0 (none after extract/verify) url=%s",
                    job_id,
                    url[:120],
                )
                return 0

            for opp in complete:
                if "metadata" not in opp or not isinstance(opp["metadata"], dict):
                    opp["metadata"] = {}
                opp["metadata"]["sourceUrl"] = url
                if job_store == JOB_STORE_SCRAPERS:
                    opp["metadata"]["scraperId"] = job_id
                else:
                    opp["metadata"]["urlCollectionId"] = url_collection_id
                if not opp["metadata"].get("description") or not str(opp["metadata"].get("description", "")).strip():
                    opp["metadata"]["description"] = (description or opp.get("event_name") or "").strip() or ""
                src: dict = {"google_query": from_google_query, "source_url": url}
                if from_google_query:
                    q = (google_search_query or "").strip()
                    if q:
                        src["google_search_query"] = q
                opp["source"] = src

            if complete:
                existing_keys = await self.opportunity_model.find_existing_dedupe_keys(complete)
                to_insert: list[dict] = []
                seen_batch: set[tuple[str, str]] = set()
                skipped_db = 0
                skipped_batch = 0
                for opp in complete:
                    k = opportunity_dedupe_key(opp)
                    if not k:
                        continue
                    if k in existing_keys:
                        skipped_db += 1
                        continue
                    if k in seen_batch:
                        skipped_batch += 1
                        continue
                    seen_batch.add(k)
                    to_insert.append(opp)

                logger.info(
                    "[opp-pipeline] job=%s dedupe to_insert=%d skipped_already_in_db=%d skipped_batch_dup=%d",
                    url_collection_id,
                    len(to_insert),
                    skipped_db,
                    skipped_batch,
                )

                if not to_insert:
                    logger.info(
                        "[opp-pipeline] job=%s completed inserted=0 (all duplicates or no valid keys) url=%s",
                        url_collection_id,
                        url[:120],
                    )
                    return 0

                inserted_ids = await self.opportunity_model.insert_many(to_insert)
                logger.info(
                    "[opp-pipeline] job=%s completed inserted=%d into Opportunities url=%s",
                    url_collection_id,
                    len(inserted_ids),
                    url[:120],
                )
                if not from_google_query:
                    await self.recent_activity_model.try_insert_activity(
                        RECENT_ACTIVITY_TYPE_OPPORTUNITIES,
                        message_opportunities_added(len(inserted_ids)),
                    )
                # Push each inserted opportunity to Pinecone (vector DB) in thread to avoid blocking
                try:
                    store = PineconeOpportunityStore()
                    if store.is_configured():
                        def _upsert_batch():
                            n_vec = 0
                            for opp, oid in zip(to_insert, inserted_ids):
                                if opp.get("isQualified"):
                                    if store.upsert_opportunity(oid, opp):
                                        n_vec += 1
                            return n_vec

                        n_pinecone = await asyncio.to_thread(_upsert_batch)
                        n_unqualified = sum(1 for o in to_insert if not o.get("isQualified"))
                        logger.info(
                            "Job %s: Pinecone upserted %d qualified vector(s); %d not qualified (Mongo only)",
                            url_collection_id,
                            n_pinecone,
                            n_unqualified,
                        )
                except Exception as pin_e:
                    logger.warning("Pinecone upsert failed for job %s: %s", url_collection_id, pin_e)
                return len(inserted_ids)
            else:
                logger.info(
                    "[opp-pipeline] job=%s completed inserted=0 (all incomplete after complete_filter) url=%s",
                    url_collection_id,
                    url[:120],
                )
                return 0
        except Exception as e:
            logger.exception("Job %s failed: %s", job_id, e)
            await self._update_job_status(job_id, {"status": "failed"}, job_store=job_store)
            return 0

    async def _run_tedx_cron_async(self) -> None:
        """
        Cron job: Search Google for Ted X opportunities, take top 5 URLs,
        and run the same scrape+extract+enrich flow as the API (with 5s delay between RapidAPI calls).
        No user_id - runs as system job.
        """
        logger.info("TedX cron job started")
        try:
            serp = SerpHelper()
            urls = serp.search(TEDX_CRON_QUERY)
            non_pdf = [u for u in (urls or []) if not is_pdf_url(u)]
            top_urls = non_pdf[:TEDX_CRON_TOP_N]
            if not top_urls:
                logger.warning("TedX cron: no URLs from SERP for query=%s", TEDX_CRON_QUERY)
                return

            logger.info("TedX cron: processing %d URLs with %ds delay between RapidAPI calls and between URLs", len(top_urls), RAPIDAPI_DELAY_SECONDS)
            for i, url in enumerate(top_urls):
                try:
                    if i > 0:
                        await asyncio.sleep(RAPIDAPI_DELAY_SECONDS)  # 5s between URLs
                    url_collection_id = await self.create_url_scrape_job(url, user_id=None)
                    await self.run_scrape_and_extract(url_collection_id, url, delay_seconds=RAPIDAPI_DELAY_SECONDS)
                except Exception as e:
                    logger.exception("TedX cron: failed for url=%s: %s", url[:80], e)
            logger.info("TedX cron job completed")
        except Exception as e:
            logger.exception("TedX cron job failed: %s", e)

    def run_tedx_daily_cron(self) -> None:
        """
        Synchronous entrypoint for APScheduler.
        Runs TedX cron in a new event loop (scheduler runs in background thread).
        """
        asyncio.run(self._run_tedx_cron_async())

    async def process_all_pending(self) -> dict:
        """
        Drain all pending UrlCollections one at a time. Used by the UrlCollection cron.

        Claims a single job (pending -> running), scrapes it to completion, then claims the
        next — so only one document is ``running`` at a time.
        """
        summary = {
            "claimed": 0,
            "completed": 0,
            "failed": 0,
            "skipped_invalid": 0,
            "unexpected_status_after_run": 0,
        }
        while True:
            claimed = await self.url_collection_model.claim_pending_jobs(limit=1)
            if not claimed:
                break
            batch = await self._process_claimed_pending(
                claimed,
                job_store=JOB_STORE_URL_COLLECTION,
            )
            for k in summary:
                summary[k] += batch.get(k, 0)
        return summary

    async def process_all_pending_scrapers(self) -> dict:
        """
        Drain all pending Scrapers (API url-scraper jobs) one at a time.
        Used by the weekly pending Scrapers cron.
        """
        summary = {
            "claimed": 0,
            "completed": 0,
            "failed": 0,
            "skipped_invalid": 0,
            "opportunities_inserted": 0,
        }
        while True:
            claimed = await self.scraper_model.claim_pending_jobs(limit=1)
            if not claimed:
                break
            batch = await self._process_claimed_pending(
                claimed,
                job_store=JOB_STORE_SCRAPERS,
            )
            for k in summary:
                summary[k] += batch.get(k, 0)
        return summary

    async def process_pending_batch(self, limit: int = 10) -> dict:
        """
        Claim up to `limit` pending UrlCollections (e.g. manual script runs) and run RapidAPI scrape + LLM extract.
        Jobs run sequentially with RAPIDAPI_DELAY_SECONDS between URLs.
        """
        return await self._process_claimed_pending(
            await self.url_collection_model.claim_pending_jobs(limit=limit),
            job_store=JOB_STORE_URL_COLLECTION,
        )

    async def _process_claimed_pending(
        self,
        claimed: list,
        *,
        job_store: str = JOB_STORE_URL_COLLECTION,
    ) -> dict:
        summary = {
            "claimed": len(claimed),
            "completed": 0,
            "failed": 0,
            "skipped_invalid": 0,
            "opportunities_inserted": 0,
        }
        for i, doc in enumerate(claimed):
            job_id = str(doc["_id"])
            url = (doc.get("url") or "").strip()
            if not url:
                await self._update_job_status(job_id, {"status": "failed"}, job_store=job_store)
                summary["skipped_invalid"] += 1
                continue
            if is_pdf_url(url):
                await self._update_job_status(job_id, {"status": "failed"}, job_store=job_store)
                summary["skipped_invalid"] += 1
                continue
            if i > 0:
                await asyncio.sleep(RAPIDAPI_DELAY_SECONDS)
            n = await self.run_scrape_and_extract(
                job_id,
                url,
                delay_seconds=RAPIDAPI_DELAY_SECONDS,
                from_google_query=False,
                job_store=job_store,
            )
            if job_store == JOB_STORE_SCRAPERS:
                final = await self.scraper_model.get_doc_by_id(job_id)
            else:
                final = await self.url_collection_model.get_by_id(job_id)
            st = (final or {}).get("status")
            if st == "completed":
                summary["completed"] += 1
                summary["opportunities_inserted"] += n
            else:
                summary["failed"] += 1
        return summary
