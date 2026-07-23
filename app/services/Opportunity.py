"""Service for Opportunities CRUD operations and speaker-based matching via Pinecone."""

import asyncio
import logging
import os
from datetime import date, datetime
from typing import List

logger = logging.getLogger(__name__)

from app.models.Opportunity import OpportunityModel, deadline_time_filter_query
from app.models.SpeakerProfile import SpeakerProfileModel
from app.models.MatchedOpportunities import MatchedOpportunitiesModel
from app.models.OpportunityActivity import OpportunityActivityModel
from app.helpers.PineconeOpportunityStore import PineconeOpportunityStore, OpportunityTextBuilder
from app.helpers.OpportunityQualifier import _application_deadline_iso
from app.email.deadline_approaching_notification import parse_metadata_deadline_date
from app.agents.OpportunitySpeakerMatchAgent import OpportunitySpeakerMatchAgent


# Minimum similarity score (0–1) to consider a match; below 50% we do not consider it matching
MIN_SIMILARITY_THRESHOLD = 0.4


def _is_future_opportunity(opp: dict) -> bool:
    """True if opportunity has start_date on or after today; otherwise False."""
    start = opp.get("start_date")
    if not start or not str(start).strip():
        return False
    s = str(start).strip()[:10]
    try:
        start_date = datetime.strptime(s, "%Y-%m-%d").date()
        return start_date >= date.today()
    except ValueError:
        return False


def _submission_deadline_passed(opp: dict) -> bool:
    """
    True when a parseable submission deadline exists and is before today.
    Missing / unparseable deadlines return False (treat as still open for matching).
    """
    deadline_iso = _application_deadline_iso(opp)
    if deadline_iso:
        try:
            return date.fromisoformat(deadline_iso[:10]) < date.today()
        except ValueError:
            pass
    meta_deadline = parse_metadata_deadline_date(opp)
    if meta_deadline is not None:
        return meta_deadline < date.today()
    return False


class OpportunityService:
    def __init__(
        self,
        opportunity_model: OpportunityModel = None,
        speaker_profile_model: SpeakerProfileModel = None,
        pinecone_store: PineconeOpportunityStore = None,
        matched_opportunities_model: MatchedOpportunitiesModel = None,
        opportunity_activity_model: OpportunityActivityModel = None,
    ):
        self.model = opportunity_model or OpportunityModel()
        self.speaker_profile_model = speaker_profile_model or SpeakerProfileModel()
        self.pinecone_store = pinecone_store or PineconeOpportunityStore()
        self.matched_opportunities_model = matched_opportunities_model or MatchedOpportunitiesModel()
        self.opportunity_activity_model = opportunity_activity_model or OpportunityActivityModel()

    async def list_opportunities(
        self,
        page: int = 1,
        limit: int = 10,
        sort_by_start_date: str | None = None,
        sort_by_end_date: str | None = None,
        sort_by_created_at: str | None = None,
        sort_by_deadline: str | None = "asc",
        time_filter: str | None = None,
    ) -> dict:
        """
        List opportunities with pagination. page is 1-based.
        Optional sort by start_date, end_date, and/or created_at (asc/desc).
        sort_by_deadline: asc (default) | desc; 'deadline not found' always last.
        time_filter: future | past | none (all). Filters on submissionInfo.deadline.
        """
        skip = (page - 1) * limit
        sort_by = self._build_sort(
            sort_by_start_date,
            sort_by_end_date,
            sort_by_created_at,
        )
        deadline_sort = (sort_by_deadline or "asc").strip().lower() or "asc"
        if deadline_sort not in ("asc", "desc"):
            raise ValueError("sort_by_deadline must be asc or desc")
        query = deadline_time_filter_query(time_filter)
        opportunities = await self.model.get_list(
            skip=skip,
            limit=limit,
            sort_by=sort_by,
            query=query,
            sort_by_deadline=deadline_sort,
        )
        total = await self.model.count(query=query)
        return {
            "opportunities": opportunities,
            "total": total,
            "page": page,
            "limit": limit,
            "totalPages": (total + limit - 1) // limit if limit > 0 else 0,
            "filter": (time_filter or "none").strip().lower() or "none",
            "sort_by_deadline": deadline_sort,
        }

    def _build_sort(
        self,
        sort_by_start_date: str | None,
        sort_by_end_date: str | None,
        sort_by_created_at: str | None,
    ) -> dict:
        """Build sort dict for list: start_date, end_date, and/or createdAt (1=asc, -1=desc). Default _id -1 if none."""
        order = {}
        if sort_by_start_date and sort_by_start_date.lower() in ("asc", "desc"):
            order["start_date"] = 1 if sort_by_start_date.lower() == "asc" else -1
        if sort_by_end_date and sort_by_end_date.lower() in ("asc", "desc"):
            order["end_date"] = 1 if sort_by_end_date.lower() == "asc" else -1
        if sort_by_created_at and sort_by_created_at.lower() in ("asc", "desc"):
            order["createdAt"] = 1 if sort_by_created_at.lower() == "asc" else -1
        if not order:
            return {"_id": -1}
        return order

    async def get_opportunity_by_id(self, opportunity_id: str) -> dict | None:
        """Get a single opportunity by ID. Returns None if not found."""
        doc = await self.model.get_by_id(opportunity_id)
        if doc and doc.get("_id") is not None:
            doc["_id"] = str(doc["_id"])
        return doc

    async def delete_opportunity(self, opportunity_id: str) -> bool:
        """Delete an opportunity by ID. Returns True if deleted."""
        return await self.model.delete_by_id(opportunity_id)

    async def get_matched_opportunities_for_speaker(
        self,
        speaker_profile_id: str,
        min_results: int = 5,
        max_results: int = 10,
    ) -> List[dict]:
        """
        Get opportunities matched to a speaker profile via Pinecone vector search.
        Extracts topics, speaking_formats, delivery_mode, target_audiences, talk_description from profile,
        finds similar opportunities in Pinecone (min 5, max 10), returns full opportunity docs from MongoDB.
        Only opportunities with start_date on or after today are returned (no past opportunities).
        Only matches with similarity score >= min_score (env OPPORTUNITY_MIN_SIMILARITY_SCORE or 0.5) are included.
        """
        profile = await self.speaker_profile_model.get_profile(speaker_profile_id)
        if not profile:
            return []
        if not self.pinecone_store.is_configured():
            return []
        query_text = OpportunityTextBuilder.from_speaker_profile(profile)
        if not query_text:
            return []
        # Only consider matches with score >= 50%; env can override
        min_score = MIN_SIMILARITY_THRESHOLD
        try:
            env_val = os.getenv("OPPORTUNITY_MIN_SIMILARITY_SCORE")
            if env_val is not None:
                min_score = float(env_val)
        except (TypeError, ValueError):
            min_score = MIN_SIMILARITY_THRESHOLD
        # Query more than max_results so we have buffer after filtering by score and past opportunities
        # Run Pinecone (sync/blocking) in thread pool so the event loop is not blocked
        top_k = max(max_results * 3, 30)
        opportunity_ids, scores = await asyncio.to_thread(
            self.pinecone_store.query_similar_opportunity_ids,
            query_text,
            top_k,
            min_score,
        )
        if not opportunity_ids:
            return []
        id_to_score = dict(zip(opportunity_ids, scores))
        opportunities = await self.model.get_by_ids(opportunity_ids)
        # Keep only future opportunities (start_date >= today)
        future_opportunities = [o for o in opportunities if _is_future_opportunity(o)]
        # Return up to max_results, preserving similarity order; attach similarity_score to each
        result = future_opportunities[:max_results]
        for opp in result:
            if opp.get("_id") is not None:
                opp["_id"] = str(opp["_id"])
            opp["similarity_score"] = round(id_to_score.get(str(opp.get("_id")), 0.0), 4)
        return result

    async def start_matching_run(self, speaker_profile_id: str) -> str | None:
        """
        Delete any existing matchedOpportunities doc for this speaker, then create a new
        entry with status 'processing'. Returns the new entry _id (str) or None on failure.
        """
        await self.matched_opportunities_model.delete_by_speaker_id(speaker_profile_id)
        return await self.matched_opportunities_model.create_processing_entry(speaker_profile_id)

    async def run_matching_and_save(
        self,
        speaker_profile_id: str,
        match_agent: OpportunitySpeakerMatchAgent = None,
        matched_entry_id: str | None = None,
        send_matched_email: bool | None = None,
    ) -> None:
        """
        Run vector matching, then filter each opportunity with an AI agent (does it match the speaker?),
        and save only the agent-approved opportunity ids to matchedOpportunities.
        Past submission-deadline matches are marked isExpired on opportunityActivity and excluded
        from the saved match list and new-opportunity email.
        When matched_entry_id is provided (from match-by-speaker flow), updates that entry to status 'completed'.

        send_matched_email: None = send only for immediate new_opportunity frequency (weekly defers to cron);
        True = always try to send after match; False = never send.
        """
        def _finish(opportunity_ids: list):
            if matched_entry_id:
                return self.matched_opportunities_model.update_entry_completed(
                    matched_entry_id, opportunity_ids
                )
            return self.matched_opportunities_model.upsert_by_speaker_id(
                speaker_profile_id, opportunity_ids
            )

        profile = await self.speaker_profile_model.get_profile(speaker_profile_id)
        if not profile:
            await _finish([])
            return
        opportunities = await self.get_matched_opportunities_for_speaker(speaker_profile_id)
        if not opportunities:
            await _finish([])
            return
        agent = match_agent or OpportunitySpeakerMatchAgent()
        filtered = []
        for opp in opportunities:
            is_match = await asyncio.to_thread(agent.is_match, profile, opp)
            if is_match:
                filtered.append(opp)

        active: List[dict] = []
        expired: List[dict] = []
        for opp in filtered:
            if _submission_deadline_passed(opp):
                expired.append(opp)
            else:
                active.append(opp)

        for opp in expired:
            oid = opp.get("_id")
            if oid is None:
                continue
            try:
                await self.opportunity_activity_model.upsert_fields(
                    speaker_profile_id,
                    str(oid),
                    {"isExpired": True},
                )
            except Exception as e:
                logger.warning(
                    "Failed to mark opportunityActivity expired speaker_id=%s opportunityId=%s: %s",
                    speaker_profile_id,
                    oid,
                    e,
                )

        opportunity_ids = [str(o.get("_id")) for o in active if o.get("_id") is not None]
        await _finish(opportunity_ids)
        if active and await self._should_send_matched_email_after_match(
            profile,
            send_matched_email=send_matched_email,
        ):
            try:
                from app.services.MatchedOpportunitiesEmailService import MatchedOpportunitiesEmailService

                mailer = MatchedOpportunitiesEmailService(
                    opportunity_service=self,
                    speaker_profile_model=self.speaker_profile_model,
                )
                await mailer.send_matched_opportunities_email(
                    speaker_profile_id,
                    opportunity_documents=active,
                )
            except Exception as e:
                logger.warning("Matched opportunities notification email failed: %s", e)

    async def _should_send_matched_email_after_match(
        self,
        profile: dict,
        *,
        send_matched_email: bool | None,
    ) -> bool:
        if send_matched_email is False:
            return False
        if send_matched_email is True:
            return True

        from app.email.notification_delivery import is_weekly_new_opportunity_frequency
        from app.services.NotificationDeliveryService import NotificationDeliveryService

        delivery = NotificationDeliveryService()
        frequency = await delivery.get_frequency_for_speaker(profile, "new_opportunity")
        return not is_weekly_new_opportunity_frequency(frequency)

    async def get_matched_opportunities_by_speaker_id(
        self, speaker_profile_id: str
    ) -> tuple[List[dict], str]:
        """
        Get matched opportunities stored for this speaker (from matchedOpportunities collection).
        Returns (list of full opportunity documents, status) where status is 'processing' or 'completed'.
        """
        doc = await self.matched_opportunities_model.get_by_speaker_id(speaker_profile_id)
        if not doc:
            return [], "completed"
        status = (doc.get("status") or "completed").lower()
        opportunity_ids = doc.get("opportunities") or []
        if not opportunity_ids:
            return [], status
        opportunities = await self.model.get_by_ids(opportunity_ids)
        for opp in opportunities:
            if opp.get("_id") is not None:
                opp["_id"] = str(opp["_id"])
        return opportunities, status
