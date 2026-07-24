"""Cron jobs: drain pending GoogleQueries, UrlCollections, and Scrapers (API url-scraper)."""

from __future__ import annotations

import logging
import os
from typing import Any

from app.services.GoogleQueryScraper import GoogleQueryScraperService
from app.services.UrlScraperRapidAPI import UrlScraperRapidAPIService

logger = logging.getLogger(__name__)

_DEFAULT_CRON_TIMEOUT_SECONDS = 14400  # 4h — UrlCollection/Scrapers drains; GoogleQuery defaults to no timeout


def _cron_timeout_seconds() -> float:
    raw = (os.getenv("PENDING_SCRAPER_CRON_TIMEOUT_SECONDS") or "").strip()
    if not raw:
        return float(_DEFAULT_CRON_TIMEOUT_SECONDS)
    try:
        return max(60.0, float(raw))
    except ValueError:
        return float(_DEFAULT_CRON_TIMEOUT_SECONDS)


def _google_query_cron_timeout_seconds() -> float | None:
    """
    GoogleQuery drain is sequential and can take many hours (dozens of queries × SERP/scrape).

    Default: no timeout (wait until finished). Override with
    PENDING_GOOGLE_QUERY_CRON_TIMEOUT_SECONDS (seconds). Set to 0 for no timeout explicitly.
    """
    raw = (os.getenv("PENDING_GOOGLE_QUERY_CRON_TIMEOUT_SECONDS") or "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    if value <= 0:
        return None
    return max(60.0, value)


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

    async def run_all_pending_scrapers(self) -> dict[str, Any]:
        summary = await UrlScraperRapidAPIService().process_all_pending_scrapers()
        logger.info("Pending Scrapers cron finished: %s", summary)
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
    """Default 168h (1 week). Override with PENDING_GOOGLE_QUERY_CRON_INTERVAL_HOURS."""
    return _interval_hours("PENDING_GOOGLE_QUERY_CRON_INTERVAL_HOURS", 168)


def pending_url_collections_cron_interval_hours() -> int:
    return _interval_hours("PENDING_URL_COLLECTION_CRON_INTERVAL_HOURS", 72)


def pending_scrapers_cron_interval_hours() -> int:
    """Default 168h (1 week) for API Scrapers jobs. Override with PENDING_SCRAPERS_CRON_INTERVAL_HOURS."""
    return _interval_hours("PENDING_SCRAPERS_CRON_INTERVAL_HOURS", 168)


def run_pending_google_queries_cron_sync() -> None:
    """Synchronous entrypoint for APScheduler — all pending GoogleQueries (sequential)."""

    async def _run() -> None:
        await PendingScraperCronService().run_all_pending_google_queries()

    try:
        from app.helpers.scheduler_async import run_coroutine_on_app_loop

        timeout = _google_query_cron_timeout_seconds()
        logger.info(
            "Pending GoogleQuery cron starting (timeout=%s)",
            "none" if timeout is None else f"{timeout}s",
        )
        run_coroutine_on_app_loop(_run(), timeout=timeout)
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


def run_pending_scrapers_cron_sync() -> None:
    """Synchronous entrypoint for APScheduler — all pending Scrapers (API url-scraper)."""

    async def _run() -> None:
        await PendingScraperCronService().run_all_pending_scrapers()

    try:
        from app.helpers.scheduler_async import run_coroutine_on_app_loop

        logger.info("Pending Scrapers cron starting")
        run_coroutine_on_app_loop(_run(), timeout=_cron_timeout_seconds())
    except Exception:
        logger.exception("Pending Scrapers cron top-level failure")
