from typing import Any, Optional

from app.models.OpportunityActivity import OpportunityActivityModel


class OpportunityActivityService:
    def __init__(self, model: Optional[OpportunityActivityModel] = None):
        self.model = model or OpportunityActivityModel()

    def _validate_ids(self, speaker_id: str, opportunity_id: str) -> None:
        if not self.model.is_valid_object_id(speaker_id):
            raise ValueError("Invalid speaker_id")
        if not self.model.is_valid_object_id(opportunity_id):
            raise ValueError("Invalid opportunityId")

    def _serialize_public(self, doc: Optional[dict]) -> dict:
        if not doc:
            return {}
        raw_outcomes = doc.get("outcomes")
        outcomes_val: Optional[str]
        if raw_outcomes is None:
            outcomes_val = None
        elif isinstance(raw_outcomes, str):
            outcomes_val = raw_outcomes
        else:
            outcomes_val = str(raw_outcomes)

        return {
            "opportunityId": doc.get("opportunityId", ""),
            "speaker_id": doc.get("speaker_id", ""),
            "isWishlist": bool(doc.get("isWishlist", False)),
            "isApplied": bool(doc.get("isApplied", False)),
            "isAccepted": bool(doc.get("isAccepted", False)),
            "isExpired": bool(doc.get("isExpired", False)),
            "isArchived": bool(doc.get("isArchived", False)),
            "outcomes": outcomes_val,
        }

    async def get_activity(self, speaker_id: str, opportunity_id: str) -> dict:
        self._validate_ids(speaker_id, opportunity_id)
        doc = await self.model.get_one(speaker_id, opportunity_id)
        if doc:
            return self._serialize_public(doc)
        return {
            "opportunityId": str(opportunity_id),
            "speaker_id": str(speaker_id),
            "isWishlist": False,
            "isApplied": False,
            "isAccepted": False,
            "isExpired": False,
            "isArchived": False,
            "outcomes": None,
        }

    async def update_activity(
        self,
        speaker_id: str,
        opportunity_id: str,
        is_wishlist: Optional[bool] = None,
        is_applied: Optional[bool] = None,
        is_accepted: Optional[bool] = None,
        is_expired: Optional[bool] = None,
        is_archived: Optional[bool] = None,
        outcomes: Optional[str] = None,
        outcomes_provided: bool = False,
    ) -> dict:
        self._validate_ids(speaker_id, opportunity_id)
        set_fields: dict[str, Any] = {}
        if is_wishlist is not None:
            set_fields["isWishlist"] = is_wishlist
        if is_applied is not None:
            set_fields["isApplied"] = is_applied
        if is_accepted is not None:
            set_fields["isAccepted"] = is_accepted
        if is_expired is not None:
            set_fields["isExpired"] = is_expired
        if is_archived is not None:
            set_fields["isArchived"] = is_archived
        if outcomes_provided:
            set_fields["outcomes"] = outcomes
        if not set_fields:
            return await self.get_activity(speaker_id, opportunity_id)

        doc = await self.model.upsert_fields(speaker_id, opportunity_id, set_fields)
        return self._serialize_public(doc)
