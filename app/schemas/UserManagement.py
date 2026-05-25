from datetime import datetime
from typing import Any, List, Optional

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.schemas.User import SubscriptionType, UserType


class SpeakerProfileSummary(BaseModel):
    """Speaker profile fields exposed on user management APIs (no conversation)."""

    id: str = Field(..., description="Profile document id")
    full_name: Optional[str] = None
    email: Optional[str] = None
    current_step: Optional[str] = None
    isCompleted: Optional[bool] = None
    createdAt: Optional[datetime] = None
    updatedAt: Optional[datetime] = None

    class Config:
        populate_by_name = True


class UserSubscriptionPublic(BaseModel):
    isSubscriptionTaken: bool = False
    subscriptionType: SubscriptionType = SubscriptionType.FREE
    subscribedAt: Optional[datetime] = None
    planLimits: Optional[dict[str, Any]] = None
    planUsage: Optional[dict[str, Any]] = None


class UserPublic(BaseModel):
    """User fields returned by admin user APIs (no password)."""

    id: str
    email: EmailStr
    fullName: str
    userType: UserType
    address: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None
    zip: Optional[str] = None
    profilePicture: Optional[str] = None
    phone: Optional[str] = None
    adminId: Optional[str] = None
    subscription: Optional[UserSubscriptionPublic] = None
    createdOn: Optional[datetime] = None
    updatedOn: Optional[datetime] = None


class UserWithSpeakerProfiles(BaseModel):
    user: UserPublic
    speakerProfiles: List[SpeakerProfileSummary] = Field(default_factory=list)


class UsersListPagination(BaseModel):
    total: int
    totalPages: int
    currentPage: int
    limit: int


class UsersWithProfilesListData(BaseModel):
    users: List[UserWithSpeakerProfiles]
    pagination: UsersListPagination


class AddSpeakerProfileForUserBody(BaseModel):
    """Admin: create a new onboarding-style speaker profile for a user."""

    full_name: str = Field(..., min_length=1, max_length=200, description="Initial display name (full_name step).")


class LinkSpeakerProfilesToUserBody(BaseModel):
    """Admin: set user_id on existing speaker profile documents (reassigns if they belonged to another user)."""

    speaker_profile_ids: List[str] = Field(
        ...,
        min_length=1,
        max_length=200,
        description="MongoDB _id values of speaker_profiles to attach to this user.",
    )

    @field_validator("speaker_profile_ids")
    @classmethod
    def strip_ids(cls, v: List[str]) -> List[str]:
        out = [str(x).strip() for x in v if x is not None and str(x).strip()]
        if not out:
            raise ValueError("speaker_profile_ids must contain at least one non-empty id")
        return out
