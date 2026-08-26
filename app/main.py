import asyncio
import logging
import os

import uvicorn
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from dotenv import load_dotenv
from fastapi import Depends, FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from starlette.responses import RedirectResponse

from app.controllers import (
    Auth,
    Common,
    Dashboard,
    GoogleQueryScraper,
    NotificationSettings,
    Opportunity,
    Profile,
    Scraper,
    SpeakerOptions,
    SpeakerProfileOnboarding,
    Subscriptions,
    UrlScraperRapidAPI,
    Users,
)
from app.dependencies import cleanup_resources
from app.helpers.Database import MongoDB
from app.helpers.scheduler_async import register_app_event_loop
from app.middleware.Cors import add_cors_middleware
from app.middleware.GlobalErrorHandling import GlobalErrorHandlingMiddleware
from app.middleware.JWTVerification import jwt_validator
from app.services.DeadlineApproachingCronService import run_deadline_approaching_cron_sync
from app.services.OpportunityExpiryCronService import run_opportunity_expiry_cron_sync
from app.services.PendingNotificationEmailCronService import run_pending_notification_email_cron_sync
from app.services.PendingScraperCronService import (
    pending_url_collections_cron_interval_hours,
    run_pending_google_queries_cron_sync,
    run_pending_url_collections_cron_sync,
    run_pending_scrapers_cron_sync,
)
from app.services.SubmissionReminderCronService import run_submission_reminder_cron_sync
from app.services.WeeklyNewOpportunityCronService import run_weekly_new_opportunity_cron_sync
from app.services.Subscriptions import init_stripe_from_env

load_dotenv()

_cron_scheduler = BackgroundScheduler(
    job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 300},
)

# Configure logging for URL scraper and LLM extraction
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
for name in (
    "app.helpers.RapidAPIScraper",
    "app.helpers.SpeakingOpportunityExtractor",
    "app.services.UrlScraperRapidAPI",
    "app.services.GoogleQueryScraper",
    "app.services.PendingScraperCronService",
):
    logging.getLogger(name).setLevel(logging.INFO)

app = FastAPI(
    title="HD AI",
    description="HD AI Backend API's",
    version="1.0.0",
    docs_url="/api-docs",
    redoc_url="/api-redoc",
)

# Middleware
app.add_middleware(GlobalErrorHandlingMiddleware)
app.add_middleware(GZipMiddleware, minimum_size=1000)
add_cors_middleware(app)

# Routes
app.include_router(Auth.router)
app.include_router(Profile.router, dependencies=[Depends(jwt_validator)])
app.include_router(Common.router, dependencies=[Depends(jwt_validator)])
app.include_router(SpeakerProfileOnboarding.router)
app.include_router(SpeakerOptions.router)
app.include_router(Scraper.router, dependencies=[Depends(jwt_validator)])
app.include_router(UrlScraperRapidAPI.router, dependencies=[Depends(jwt_validator)])
app.include_router(GoogleQueryScraper.router, dependencies=[Depends(jwt_validator)])
app.include_router(Opportunity.router, dependencies=[Depends(jwt_validator)])
app.include_router(Dashboard.router, dependencies=[Depends(jwt_validator)])
app.include_router(Users.router, dependencies=[Depends(jwt_validator)])
app.include_router(Subscriptions.public_router)
app.include_router(Subscriptions.auth_router, dependencies=[Depends(jwt_validator)])
app.include_router(NotificationSettings.router, dependencies=[Depends(jwt_validator)])


@app.on_event("startup")
async def startup_event():
    connection_string = os.getenv("MONGODB_CONNECTION_STRING")
    MongoDB.connect(connection_string)
    print("MongoDB connected (async with Motor)")
    init_stripe_from_env()

    register_app_event_loop(asyncio.get_running_loop())

    log = logging.getLogger(__name__)

    _cron_scheduler.add_job(
        run_submission_reminder_cron_sync,
        IntervalTrigger(minutes=60),
        id="submission_reminder_cron",
    )
    log.info("Submission reminder cron registered (every 60 min)")

    _cron_scheduler.add_job(
        run_pending_notification_email_cron_sync,
        IntervalTrigger(minutes=1),
        id="pending_notification_email_cron",
    )
    log.info("Pending notification email cron registered (every 1 min)")

    # Default 24h; set DEADLINE_APPROACHING_CRON_INTERVAL_MINUTES to override (e.g. 1 for tests).
    raw_min = (os.getenv("DEADLINE_APPROACHING_CRON_INTERVAL_MINUTES") or "").strip()
    if raw_min:
        try:
            interval_min = max(1, int(raw_min))
        except ValueError:
            interval_min = 1
        deadline_trigger = IntervalTrigger(minutes=interval_min)
        deadline_interval_desc = f"{interval_min} min"
    else:
        try:
            interval_h = max(1, int(os.getenv("DEADLINE_APPROACHING_CRON_INTERVAL_HOURS", "24")))
        except ValueError:
            interval_h = 24
        deadline_trigger = IntervalTrigger(hours=interval_h)
        deadline_interval_desc = f"{interval_h} h"

    _cron_scheduler.add_job(
        run_deadline_approaching_cron_sync,
        deadline_trigger,
        id="deadline_approaching_cron",
    )
    log.info("Deadline approaching cron registered (%s)", deadline_interval_desc)

    # Marks opportunityActivity.isExpired. Three times daily, 6 hours apart.
    # For local testing, use IntervalTrigger(minutes=1).
    _cron_scheduler.add_job(
        run_opportunity_expiry_cron_sync,
        CronTrigger(hour="0,6,12", minute=0, timezone="UTC"),
        id="opportunity_expiry_cron",
    )
    log.info("Opportunity expiry cron registered (00:00, 06:00, 12:00 UTC)")

    enable_gq_cron = (os.getenv("ENABLE_PENDING_GOOGLE_QUERY_CRON") or "true").strip().lower()
    if enable_gq_cron in ("0", "false", "no", "off"):
        log.info("Pending GoogleQuery scraper cron disabled via ENABLE_PENDING_GOOGLE_QUERY_CRON")
    else:
        _cron_scheduler.add_job(
            run_pending_google_queries_cron_sync,
            CronTrigger(day_of_week="mon", hour=16, minute=0, timezone="Asia/Kolkata"),
            id="pending_google_queries_cron",
            replace_existing=True,
        )
        log.info(
            "Pending GoogleQuery scraper cron registered "
            "(every Mon 16:00 Asia/Kolkata, sequential claim-one-process-one)",
        )

    url_collection_cron_h = pending_url_collections_cron_interval_hours()
    _cron_scheduler.add_job(
        run_pending_url_collections_cron_sync,
        IntervalTrigger(hours=url_collection_cron_h),
        id="pending_url_collections_cron",
    )
    log.info(
        "Pending UrlCollection scraper cron registered (every %s h, all pending entries)",
        url_collection_cron_h,
    )

    enable_scrapers_cron = (os.getenv("ENABLE_PENDING_SCRAPERS_CRON") or "true").strip().lower()
    if enable_scrapers_cron in ("0", "false", "no", "off"):
        log.info("Pending Scrapers cron disabled via ENABLE_PENDING_SCRAPERS_CRON")
    else:
        _cron_scheduler.add_job(
            run_pending_scrapers_cron_sync,
            CronTrigger(day_of_week="mon", hour=16, minute=0, timezone="Asia/Kolkata"),
            id="pending_scrapers_cron",
            replace_existing=True,
        )
        log.info(
            "Pending Scrapers cron registered "
            "(every Mon 16:00 Asia/Kolkata, sequential claim-one-process-one)",
        )

    _cron_scheduler.add_job(
        run_weekly_new_opportunity_cron_sync,
        CronTrigger(day_of_week="mon", hour=9, minute=0, timezone="UTC"),
        id="weekly_new_opportunity_cron",
    )
    log.info(
        "Weekly notification cron registered (new_opportunity + pitch_ready, mon 09:00 UTC)",
    )

    _cron_scheduler.start()
    log.info("Background scheduler started")


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup resources on shutdown"""
    if _cron_scheduler.running:
        _cron_scheduler.shutdown(wait=False)
    cleanup_resources()
    if MongoDB.client:
        MongoDB.client.close()
    print("App shutdown complete - resources cleaned up")


@app.get("/")
def api_docs():
    return RedirectResponse(url="/api-docs")


if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=3003, reload=True)
