"""Cron: mark opportunityActivity.isExpired for a speaker's matched opps whose deadline has passed.

Does not change opportunity documents or matchedOpportunities id lists — activity flags only.
Skips rows already applied or accepted. Expired is exclusive (other flags cleared).
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from app.email.deadline_approaching_notification import parse_metadata_deadline_date
from app.models.MatchedOpportunities import MatchedOpportunitiesModel
from app.models.Opportunity import OpportunityModel
from app.models.OpportunityActivity import OpportunityActivityModel
from app.services.OpportunityActivity import EXCLUSIVE_EXPIRED_FIELDS

logger = logging.getLogger(__name__)


def _submission_deadline_date(opportunity: dict) -> date | None:
    sub = opportunity.get("submissionInfo")
    if not isinstance(sub, dict):
        return None
    raw = str(sub.get("deadline") or "").strip()
    if not raw or raw.lower() == "deadline not found":
        return None
    try:
        return datetime.strptime(raw[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def opportunity_deadline_is_past(opportunity: dict, *, today: date) -> bool:
    """True when any parseable deadline (metadata or submissionInfo) is before today."""
    dates: list[date] = []
    meta = parse_metadata_deadline_date(opportunity)
    if meta:
        dates.append(meta)
    sub = _submission_deadline_date(opportunity)
    if sub:
        dates.append(sub)
    return any(d < today for d in dates)


class OpportunityExpiryCronService:
    def __init__(
        self,
        matched_opportunities_model: MatchedOpportunitiesModel | None = None,
        opportunity_model: OpportunityModel | None = None,
        activity_model: OpportunityActivityModel | None = None,
    ):
        self.matched_opportunities_model = (
            matched_opportunities_model or MatchedOpportunitiesModel()
        )
        self.opportunity_model = opportunity_model or OpportunityModel()
        self.activity_model = activity_model or OpportunityActivityModel()

    async def run_once(self) -> dict[str, Any]:
        today = datetime.utcnow().date()
        docs = await self.matched_opportunities_model.list_all()

        marked = 0
        skipped = 0
        errors = 0
        skip_reasons: dict[str, int] = {
            "missing_speaker": 0,
            "no_opportunity_ids": 0,
            "already_expired": 0,
            "already_applied": 0,
            "already_accepted": 0,
            "deadline_still_open": 0,
            "no_parseable_deadline": 0,
            "opportunity_not_found": 0,
        }

        for doc in docs:
            speaker_id = str(doc.get("speaker_id") or "").strip()
            if not speaker_id or not self.activity_model.is_valid_object_id(speaker_id):
                skipped += 1
                skip_reasons["missing_speaker"] += 1
                continue

            raw_ids = doc.get("opportunities") or []
            opportunity_ids = [
                str(oid).strip()
                for oid in raw_ids
                if str(oid).strip() and self.activity_model.is_valid_object_id(str(oid))
            ]
            if not opportunity_ids:
                skipped += 1
                skip_reasons["no_opportunity_ids"] += 1
                continue

            try:
                opportunities = await self.opportunity_model.get_by_ids(opportunity_ids)
                opp_by_id = {str(o.get("_id")): o for o in opportunities if o.get("_id") is not None}

                for opportunity_id in opportunity_ids:
                    opp = opp_by_id.get(opportunity_id)
                    if not opp:
                        skipped += 1
                        skip_reasons["opportunity_not_found"] += 1
                        continue

                    existing = await self.activity_model.get_one(speaker_id, opportunity_id)
                    if existing and bool(existing.get("isExpired")):
                        skipped += 1
                        skip_reasons["already_expired"] += 1
                        continue
                    if existing and bool(existing.get("isAccepted")):
                        skipped += 1
                        skip_reasons["already_accepted"] += 1
                        continue
                    if existing and bool(existing.get("isApplied")):
                        skipped += 1
                        skip_reasons["already_applied"] += 1
                        continue

                    meta = parse_metadata_deadline_date(opp)
                    sub = _submission_deadline_date(opp)
                    if meta is None and sub is None:
                        skipped += 1
                        skip_reasons["no_parseable_deadline"] += 1
                        continue

                    if not opportunity_deadline_is_past(opp, today=today):
                        skipped += 1
                        skip_reasons["deadline_still_open"] += 1
                        continue

                    await self.activity_model.upsert_fields(
                        speaker_id,
                        opportunity_id,
                        dict(EXCLUSIVE_EXPIRED_FIELDS),
                    )
                    marked += 1
            except Exception:
                logger.exception(
                    "Opportunity expiry cron failed speaker_id=%s",
                    speaker_id,
                )
                errors += 1

        return {
            "speakers": len(docs),
            "marked": marked,
            "skipped": skipped,
            "errors": errors,
            "skip_reasons": skip_reasons,
        }


def run_opportunity_expiry_cron_sync() -> None:
    """Synchronous entrypoint for APScheduler (Motor must run on the FastAPI event loop)."""

    async def _run() -> None:
        summary = await OpportunityExpiryCronService().run_once()
        if summary["marked"] or summary["errors"] or summary["speakers"]:
            logger.info("Opportunity expiry cron: %s", summary)

    try:
        from app.helpers.scheduler_async import run_coroutine_on_app_loop

        run_coroutine_on_app_loop(_run(), timeout=300)
    except Exception:
        logger.exception("Opportunity expiry cron top-level failure")
