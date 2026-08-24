from typing import Any, Optional

from app.models.OpportunityActivity import OpportunityActivityModel

_ACTIVITY_TRUE_PRIORITY = (
    "isExpired",
    "isArchived",
    "isAccepted",
    "isApplied",
    "isWishlist",
)

EXCLUSIVE_EXPIRED_FIELDS = {
    "isExpired": True,
    "isWishlist": False,
    "isApplied": False,
    "isAccepted": False,
    "isArchived": False,
}


def default_public_activity(speaker_id: str, opportunity_id: str) -> dict:
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


def new_opportunity_email_skip_reason(activity: Optional[dict]) -> Optional[str]:
    """Skip new-match emails for rows that are no longer new."""
    if not activity:
        return None
    if activity.get("isArchived"):
        return "archived"
    if activity.get("isExpired"):
        return "expired"
    if activity.get("isAccepted"):
        return "accepted"
    if activity.get("isApplied"):
        return "applied"
    return None


def pitch_ready_email_skip_reason(activity: Optional[dict]) -> Optional[str]:
    """Skip pitch-ready emails for archived or expired rows."""
    if not activity:
        return None
    if activity.get("isArchived"):
        return "archived"
    if activity.get("isExpired"):
        return "expired"
    return None


def exclusive_activity_set_fields(flag_updates: dict[str, bool]) -> dict[str, bool]:
    """If any flag is set true, keep only that winner; otherwise apply the requested falses."""
    winner = None
    for key in _ACTIVITY_TRUE_PRIORITY:
        if flag_updates.get(key) is True:
            winner = key
            break
    if winner is None:
        return flag_updates
    return {
        "isWishlist": winner == "isWishlist",
        "isApplied": winner == "isApplied",
        "isAccepted": winner == "isAccepted",
        "isExpired": winner == "isExpired",
        "isArchived": winner == "isArchived",
    }


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

    def public_activity(
        self,
        speaker_id: str,
        opportunity_id: str,
        doc: Optional[dict] = None,
    ) -> dict:
        if doc:
            return self._serialize_public(doc)
        return default_public_activity(speaker_id, opportunity_id)

    async def get_activity(self, speaker_id: str, opportunity_id: str) -> dict:
        self._validate_ids(speaker_id, opportunity_id)
        doc = await self.model.get_one(speaker_id, opportunity_id)
        return self.public_activity(speaker_id, opportunity_id, doc)

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
        existing = await self.model.get_one(speaker_id, opportunity_id)
        existing_expired = bool(existing and existing.get("isExpired"))

        set_fields: dict[str, Any] = {}
        if outcomes_provided:
            set_fields["outcomes"] = outcomes

        flag_updates: dict[str, bool] = {}
        if is_wishlist is not None:
            flag_updates["isWishlist"] = is_wishlist
        if is_applied is not None:
            flag_updates["isApplied"] = is_applied
        if is_accepted is not None:
            flag_updates["isAccepted"] = is_accepted
        if is_expired is not None:
            flag_updates["isExpired"] = is_expired
        if is_archived is not None:
            flag_updates["isArchived"] = is_archived

        if existing_expired:
            if flag_updates or outcomes_provided:
                set_fields.update(dict(EXCLUSIVE_EXPIRED_FIELDS))
        elif flag_updates:
            set_fields.update(exclusive_activity_set_fields(flag_updates))

        if not set_fields:
            return self.public_activity(speaker_id, opportunity_id, existing)

        doc = await self.model.upsert_fields(speaker_id, opportunity_id, set_fields)
        return self._serialize_public(doc)
