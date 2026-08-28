"""
Catch-up cron: verify Opportunities that were inserted without isVerified
(e.g. created before scrape stamped the flag).

Uses the same scrape + LLM gate as scripts/verify_opportunities.py.
New scrapes should already set isVerified=true in OpportunityQualifier; this job
covers the backlog and any edge cases that slipped through.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from copy import deepcopy
from datetime import datetime
from typing import Any, Dict, List, Optional

from bson import ObjectId

from app.agents.EventDetailEnricherAgent import EventDetailEnricherAgent
from app.helpers.RapidAPIScraper import RapidAPIScraper
from app.models.Opportunity import OpportunityModel

logger = logging.getLogger(__name__)

_DEFAULT_BATCH = 25
_DEFAULT_DELAY_SECONDS = 5.0
_DEFAULT_INTERVAL_HOURS = 24


def _env_int(key: str, default: int) -> int:
    raw = (os.getenv(key) or "").strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def _env_float(key: str, default: float) -> float:
    raw = (os.getenv(key) or "").strip()
    if not raw:
        return default
    try:
        return max(0.0, float(raw))
    except ValueError:
        return default


def opportunity_verify_cron_interval_hours() -> int:
    """Default 24h. Override with OPPORTUNITY_VERIFY_CRON_INTERVAL_HOURS."""
    return _env_int("OPPORTUNITY_VERIFY_CRON_INTERVAL_HOURS", _DEFAULT_INTERVAL_HOURS)


def _unverified_qualified_query() -> dict:
    return {
        "$and": [
            {"isQualified": True},
            {
                "$or": [
                    {"isVerified": {"$exists": False}},
                    {"isVerified": None},
                ]
            },
        ]
    }


def _verify_one(opp: Dict[str, Any], *, scraper, enricher, oid_str: str) -> tuple[bool, str]:
    link = (opp.get("link") or opp.get("url") or "").strip()
    event_name = (opp.get("event_name") or opp.get("title") or "").strip()
    if not link:
        return False, "Opportunity has no link/url to scrape."

    logger.info("[%s] verify scrape event=%s url=%s", oid_str, event_name[:80], link[:120])
    t0 = time.monotonic()
    try:
        result = scraper.scrape(link)
    except Exception as e:
        return False, f"Scrape failed: {e}"

    if not result.get("success"):
        err = result.get("error") or "unknown"
        return False, f"Scrape unsuccessful: {err}"

    data = result.get("data") or {}
    content = str(data.get("content") or "").strip()
    if not content:
        return False, "Scraped page content is empty."

    logger.info(
        "[%s] scrape ok in %.1fs content_chars=%d",
        oid_str,
        time.monotonic() - t0,
        len(content),
    )

    working = deepcopy(opp)
    working.pop("_id", None)
    ok, reason, _updated = enricher.verify_and_refresh_from_page_content(
        working,
        content,
        name=str(data.get("name") or "").strip(),
        description=str(data.get("description") or "").strip(),
    )
    if ok:
        return True, ""
    return False, (reason or "").strip() or "LLM determined this is not a speaking opportunity."


class OpportunityVerifyCronService:
    """Process a batch of never-verified, qualified opportunities."""

    def __init__(self, opportunity_model: OpportunityModel | None = None):
        self.opportunity_model = opportunity_model or OpportunityModel()

    async def process_unverified_batch(
        self,
        *,
        limit: Optional[int] = None,
        delay_seconds: Optional[float] = None,
    ) -> dict[str, Any]:
        limit = limit if limit is not None else _env_int("OPPORTUNITY_VERIFY_CRON_BATCH_SIZE", _DEFAULT_BATCH)
        delay_seconds = (
            delay_seconds
            if delay_seconds is not None
            else _env_float("OPPORTUNITY_VERIFY_CRON_DELAY_SECONDS", _DEFAULT_DELAY_SECONDS)
        )

        summary: dict[str, Any] = {
            "scanned": 0,
            "updated": 0,
            "verified_true": 0,
            "verified_false": 0,
            "errors": 0,
            "remaining_estimate": 0,
        }

        collection = self.opportunity_model.collection
        query = _unverified_qualified_query()
        try:
            summary["remaining_estimate"] = await collection.count_documents(query)
        except Exception:
            logger.exception("Failed counting unverified opportunities")

        cursor = collection.find(query).sort([("createdAt", 1)]).limit(limit)
        docs: List[Dict[str, Any]] = [doc async for doc in cursor]
        if not docs:
            logger.info("Opportunity verify cron: nothing to process")
            return summary

        scraper = RapidAPIScraper()
        enricher = EventDetailEnricherAgent(rapidapi_scraper=scraper)
        logger.info(
            "Opportunity verify cron starting batch=%d delay=%.1fs remaining~%s",
            len(docs),
            delay_seconds,
            summary["remaining_estimate"],
        )

        for i, doc in enumerate(docs):
            if i > 0 and delay_seconds > 0:
                await asyncio.sleep(delay_seconds)

            oid = doc.get("_id")
            oid_str = str(oid)
            summary["scanned"] += 1
            try:
                is_verified, reason = await asyncio.to_thread(
                    _verify_one,
                    doc,
                    scraper=scraper,
                    enricher=enricher,
                    oid_str=oid_str,
                )
            except Exception as e:
                summary["errors"] += 1
                logger.exception("[%s] verify crashed: %s", oid_str, e)
                continue

            summary["verified_true" if is_verified else "verified_false"] += 1
            update = {
                "isVerified": is_verified,
                "verifiedAt": datetime.utcnow(),
                "reasonForUnverify": None if is_verified else (reason or "Not a speaking opportunity"),
            }
            try:
                await collection.update_one({"_id": ObjectId(oid_str)}, {"$set": update})
                summary["updated"] += 1
                logger.info(
                    "[%s] isVerified=%s reason=%s",
                    oid_str,
                    is_verified,
                    (reason or "(speaking opportunity)")[:160],
                )
            except Exception as e:
                summary["errors"] += 1
                logger.exception("[%s] Mongo update failed: %s", oid_str, e)

        logger.info("Opportunity verify cron finished: %s", summary)
        return summary


def run_opportunity_verify_cron_sync() -> None:
    """Synchronous entrypoint for APScheduler."""

    async def _run() -> None:
        await OpportunityVerifyCronService().process_unverified_batch()

    try:
        from app.helpers.scheduler_async import run_coroutine_on_app_loop

        # Cap runtime: batch_size * (delay + scrape/llm) can be long
        timeout = _env_float("OPPORTUNITY_VERIFY_CRON_TIMEOUT_SECONDS", 7200.0)
        logger.info("Opportunity verify cron starting (timeout=%.0fs)", timeout)
        run_coroutine_on_app_loop(_run(), timeout=timeout)
    except Exception:
        logger.exception("Opportunity verify cron top-level failure")
