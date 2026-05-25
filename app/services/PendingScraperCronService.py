"""Cron jobs: drain all pending GoogleQueries and UrlCollections (scrape + opportunities)."""

from __future__ import annotations

import logging
import os
from typing import Any

from app.services.GoogleQueryScraper import GoogleQueryScraperService
from app.services.UrlScraperRapidAPI import UrlScraperRapidAPIService

logger = logging.getLogger(__name__)

_DEFAULT_CRON_TIMEOUT_SECONDS = 14400  # 4h — many URLs × RapidAPI + LLM can run long


def _cron_timeout_seconds() -> float:
    raw = (os.getenv("PENDING_SCRAPER_CRON_TIMEOUT_SECONDS") or "").strip()
    if not raw:
        return float(_DEFAULT_CRON_TIMEOUT_SECONDS)
    try:
        return max(60.0, float(raw))
    except ValueError:
        return float(_DEFAULT_CRON_TIMEOUT_SECONDS)


class PendingScraperCronService:
    """Runs the same pipelines as process_pending_* scripts, with no entry cap."""

    async def run_all_pending_google_queries(self) -> dict[str, Any]:
        summary = await GoogleQueryScraperService().process_all_pending()
        logger.info("Pending GoogleQuery cron finished: %s", summary)
        return summary

    async def run_all_pending_url_collections(self) -> dict[str, Any]:
        summary = await UrlScraperRapidAPIService().process_all_pending()
        logger.info("Pending UrlCollection cron finished: %s", summary)
        return summary


def _interval_hours(env_key: str, default_hours: int = 72) -> int:
    raw = (os.getenv(env_key) or "").strip()
    if not raw:
        return default_hours
    try:
        return max(1, int(raw))
    except ValueError:
        return default_hours


def pending_google_queries_cron_interval_hours() -> int:
    return _interval_hours("PENDING_GOOGLE_QUERY_CRON_INTERVAL_HOURS", 72)


def pending_url_collections_cron_interval_hours() -> int:
    return _interval_hours("PENDING_URL_COLLECTION_CRON_INTERVAL_HOURS", 72)


def run_pending_google_queries_cron_sync() -> None:
    """Synchronous entrypoint for APScheduler — all pending GoogleQueries."""

    async def _run() -> None:
        await PendingScraperCronService().run_all_pending_google_queries()

    try:
        from app.helpers.scheduler_async import run_coroutine_on_app_loop

        run_coroutine_on_app_loop(_run(), timeout=_cron_timeout_seconds())
    except Exception:
        logger.exception("Pending GoogleQuery cron top-level failure")


def run_pending_url_collections_cron_sync() -> None:
    """Synchronous entrypoint for APScheduler — all pending UrlCollections."""

    async def _run() -> None:
        await PendingScraperCronService().run_all_pending_url_collections()

    try:
        from app.helpers.scheduler_async import run_coroutine_on_app_loop

        run_coroutine_on_app_loop(_run(), timeout=_cron_timeout_seconds())
    except Exception:
        logger.exception("Pending UrlCollection cron top-level failure")
