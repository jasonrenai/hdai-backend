import asyncio
from typing import Any, Dict, List, Optional

from bson import ObjectId

from app.models.SpeakerProfile import SpeakerProfileModel
from app.models.User import UserModel
from app.helpers.SubscriptionStripeUtil import (
    get_subscription_entitlements,
    plan_limits_from_entitlements,
)
from app.schemas.User import (
    AdminCreateUserSchema,
    AdminUpdateUserSchema,
    SubscriptionType,
    UserSchema,
    UserSubscriptionSchema,
    UserType,
    default_user_subscription,
)
from app.schemas.UserManagement import (
    AddSpeakerProfileForUserBody,
    LinkSpeakerProfilesToUserBody,
    SpeakerProfileSummary,
    UserPublic,
    UserSubscriptionPublic,
    UserWithSpeakerProfiles,
    UsersListPagination,
    UsersWithProfilesListData,
)
from datetime import datetime


def _user_to_public(user: UserSchema, speaker_profile_count: int = 0) -> UserPublic:
    d = user.model_dump(by_alias=True)
    d.pop("password", None)
    oid = d.pop("_id", None)
    return UserPublic(
        id=str(oid) if oid is not None else "",
        email=d["email"],
        fullName=d["fullName"],
        userType=d["userType"],
        address=d.get("address"),
        city=d.get("city"),
        country=d.get("country"),
        zip=d.get("zip"),
        profilePicture=d.get("profilePicture"),
        phone=d.get("phone"),
        adminId=str(d["adminId"]) if d.get("adminId") is not None else None,
        subscription=_subscription_to_public(d, speaker_profile_count),
        createdOn=d.get("createdOn"),
        updatedOn=d.get("updatedOn"),
    )


def _subscription_from_user_doc(d: dict) -> UserSubscriptionSchema:
    raw = d.get("subscription")
    if isinstance(raw, UserSubscriptionSchema):
        return raw
    if isinstance(raw, dict) and raw:
        return UserSubscriptionSchema(**raw)
    return UserSubscriptionSchema(**default_user_subscription())


def _subscription_to_public(
    d: dict, speaker_profile_count: int = 0
) -> UserSubscriptionPublic:
    subscription = _subscription_from_user_doc(d)
    plan_key = subscription.subscriptionType.value
    is_active = subscription.isSubscriptionTaken and plan_key != SubscriptionType.FREE.value
    plan_limits = None
    if is_active:
        plan_limits = plan_limits_from_entitlements(
            get_subscription_entitlements(plan_key.capitalize())
        )
    return UserSubscriptionPublic(
        isSubscriptionTaken=subscription.isSubscriptionTaken,
        subscriptionType=subscription.subscriptionType,
        subscribedAt=subscription.subscribedAt,
        planLimits=plan_limits,
        planUsage={
            "speakerProfiles": speaker_profile_count,
            "opportunities": None,
        },
    )


def _merge_subscription_update(
    user: UserSchema, patch: dict
) -> dict:
    current = _subscription_from_user_doc(user.model_dump(by_alias=True))
    merged = current.model_dump()
    for key, value in patch.items():
        if value is None:
            continue
        merged[key] = value.value if hasattr(value, "value") else value
    plan = merged.get("subscriptionType") or SubscriptionType.FREE.value
    if isinstance(plan, SubscriptionType):
        plan = plan.value
    merged["subscriptionType"] = plan
    if patch.get("isSubscriptionTaken") is None:
        merged["isSubscriptionTaken"] = plan != SubscriptionType.FREE.value
    if plan == SubscriptionType.FREE.value:
        merged["isSubscriptionTaken"] = False
    return merged


def _profile_to_summary(doc: dict) -> SpeakerProfileSummary:
    return SpeakerProfileSummary(
        id=str(doc.get("_id", "")),
        full_name=doc.get("full_name"),
        email=doc.get("email"),
        current_step=doc.get("current_step"),
        isCompleted=doc.get("isCompleted"),
        createdAt=doc.get("createdAt"),
        updatedAt=doc.get("updatedAt"),
    )


class UserManagementService:
    def __init__(self):
        self.user_model = UserModel()
        self.profile_model = SpeakerProfileModel()

    def _auth(self):
        from app.dependencies import get_auth_service

        return get_auth_service()

    async def list_users_with_profiles(
        self, page: int = 1, limit: int = 10
    ) -> Dict[str, Any]:
        try:
            filters: dict = {"userType": {"$ne": UserType.SUPER_ADMIN.value}}
            skip = (page - 1) * limit
            total, users = await asyncio.gather(
                self.user_model.get_documents_count(filters),
                self.user_model.get_users(
                    filters,
                    skip,
                    limit,
                    sort_by={"createdOn": -1, "_id": -1},
                ),
            )
            total_pages = (total + limit - 1) // limit if limit else 0
            user_ids: List[str] = []
            for u in users:
                ud = u.model_dump(by_alias=True)
                user_ids.append(str(ud.get("_id")))
            grouped = await self.profile_model.get_profiles_by_user_ids(user_ids)

            out: List[UserWithSpeakerProfiles] = []
            for u, uid in zip(users, user_ids):
                user_profiles = grouped.get(uid, [])
                summaries = [
                    _profile_to_summary(p) for p in user_profiles
                ]
                out.append(
                    UserWithSpeakerProfiles(
                        user=_user_to_public(u, speaker_profile_count=len(user_profiles)),
                        speakerProfiles=summaries,
                    )
                )

            data = UsersWithProfilesListData(
                users=out,
                pagination=UsersListPagination(
                    total=total,
                    totalPages=total_pages,
                    currentPage=page,
                    limit=limit,
                ),
            )
            return {
                "success": True,
                "data": data.model_dump(mode="json"),
                "error": None,
            }
        except Exception as e:
            return {"success": False, "data": None, "error": str(e)}

    async def get_user_with_profiles(self, user_id: str) -> Dict[str, Any]:
        try:
            try:
                oid = ObjectId(user_id)
            except Exception:
                return {
                    "success": False,
                    "data": None,
                    "error": "User not found",
                }
            user = await self.user_model.get_user({"_id": oid})
            if not user:
                return {
                    "success": False,
                    "data": None,
                    "error": "User not found",
                }
            profiles = await self.profile_model.get_profiles_by_user_id(user_id)
            summaries = [_profile_to_summary(p) for p in profiles]
            payload = UserWithSpeakerProfiles(
                user=_user_to_public(user, speaker_profile_count=len(profiles)),
                speakerProfiles=summaries,
            )
            return {
                "success": True,
                "data": payload.model_dump(mode="json"),
                "error": None,
            }
        except Exception as e:
            return {"success": False, "data": None, "error": str(e)}

    async def create_user_by_admin(
        self, user_data: AdminCreateUserSchema, admin_id: str
    ) -> Dict[str, Any]:
        return await self._auth().create_user_by_admin(user_data, admin_id)

    async def update_user_admin(
        self, user_id: str, body: AdminUpdateUserSchema
    ) -> Dict[str, Any]:
        try:
            try:
                ObjectId(user_id)
            except Exception:
                return {
                    "success": False,
                    "data": None,
                    "error": "User not found",
                }
            update_data = body.model_dump(exclude_unset=True)
            if not update_data:
                return {
                    "success": False,
                    "data": None,
                    "error": "No data provided for update.",
                }
            user = await self.user_model.get_user({"_id": ObjectId(user_id)})
            if not user:
                return {
                    "success": False,
                    "data": None,
                    "error": "User not found",
                }
            if "userType" in update_data and update_data["userType"] is not None:
                update_data["userType"] = (
                    update_data["userType"].value
                    if hasattr(update_data["userType"], "value")
                    else update_data["userType"]
                )
            if "subscription" in update_data:
                sub_patch = update_data.pop("subscription")
                if sub_patch is not None:
                    patch_dict = (
                        sub_patch.model_dump(exclude_unset=True)
                        if hasattr(sub_patch, "model_dump")
                        else dict(sub_patch)
                    )
                    update_data["subscription"] = _merge_subscription_update(user, patch_dict)
            update_data["updatedOn"] = datetime.utcnow()
            await self.user_model.update_user(user_id, update_data)
            return await self.get_user_with_profiles(user_id)
        except Exception as e:
            return {"success": False, "data": None, "error": str(e)}

    async def delete_user(self, user_id: str) -> Dict[str, Any]:
        return await self._auth().delete_user(user_id)

    async def add_speaker_profile_for_user(
        self, user_id: str, body: AddSpeakerProfileForUserBody
    ) -> Dict[str, Any]:
        try:
            try:
                uid = ObjectId(user_id)
            except Exception:
                return {
                    "success": False,
                    "data": None,
                    "error": "User not found",
                }
            user = await self.user_model.get_user({"_id": uid})
            if not user:
                return {
                    "success": False,
                    "data": None,
                    "error": "User not found",
                }
            name = body.full_name.strip()
            if not name:
                return {
                    "success": False,
                    "data": None,
                    "error": "full_name is required.",
                }
            doc = await self.profile_model.create_profile(name, user_id=str(user_id))
            pid = str(doc["_id"])
            prof = await self.profile_model.get_profile(pid)
            if not prof:
                return {
                    "success": False,
                    "data": None,
                    "error": "Profile was created but could not be loaded.",
                }
            summary = _profile_to_summary(prof)
            return {
                "success": True,
                "data": {"profile": summary.model_dump(mode="json")},
                "error": None,
            }
        except Exception as e:
            return {"success": False, "data": None, "error": str(e)}

    async def link_speaker_profiles_to_user(
        self, user_id: str, body: LinkSpeakerProfilesToUserBody
    ) -> Dict[str, Any]:
        """Set user_id on existing profiles to this user (same as create-user linking)."""
        try:
            try:
                uid = ObjectId(user_id)
            except Exception:
                return {
                    "success": False,
                    "data": None,
                    "error": "User not found",
                }
            user = await self.user_model.get_user({"_id": uid})
            if not user:
                return {
                    "success": False,
                    "data": None,
                    "error": "User not found",
                }
            link = await self.profile_model.assign_profiles_to_user(
                body.speaker_profile_ids, str(user_id)
            )
            return {
                "success": True,
                "data": {
                    "speakerProfilesLinked": link,
                    "userId": str(user_id).strip(),
                },
                "error": None,
            }
        except Exception as e:
            return {"success": False, "data": None, "error": str(e)}

    async def delete_speaker_profile_for_user(
        self, user_id: str, profile_id: str
    ) -> Dict[str, Any]:
        try:
            try:
                ObjectId(user_id)
            except Exception:
                return {
                    "success": False,
                    "data": None,
                    "error": "User not found",
                }
            user = await self.user_model.get_user({"_id": ObjectId(user_id)})
            if not user:
                return {
                    "success": False,
                    "data": None,
                    "error": "User not found",
                }
            deleted = await self.profile_model.delete_profile_for_user(
                profile_id, user_id
            )
            if not deleted:
                return {
                    "success": False,
                    "data": None,
                    "error": "Speaker profile not found or not owned by this user.",
                }
            return {
                "success": True,
                "data": {
                    "deleted": True,
                    "profileId": str(profile_id).strip(),
                    "userId": str(user_id).strip(),
                },
                "error": None,
            }
        except Exception as e:
            return {"success": False, "data": None, "error": str(e)}
