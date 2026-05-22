from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, List, Mapping, Optional

from bson import ObjectId

from app.models.User import UserModel


def as_dict(obj: Any) -> Mapping[str, Any]:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    return dict(obj)


def node_style_timestamp_ms(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt.timestamp() * 1000
    try:
        timestamp_num = float(value) if isinstance(value, str) else float(value)
    except (TypeError, ValueError):
        return None
    ts_int = int(timestamp_num)
    digit_str = str(abs(ts_int))
    if len(digit_str) == 13:
        return float(ts_int)
    return float(ts_int * 1000)


def stripe_timestamp_to_iso(value: Any) -> Optional[str]:
    ms = node_style_timestamp_ms(value)
    if ms is None:
        return None
    try:
        dt = datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)
    except (OSError, ValueError, OverflowError):
        return None
    return dt.isoformat()


async def user_doc_for_stripe(users: UserModel, user_id: str) -> Optional[dict[str, Any]]:
    if not ObjectId.is_valid(user_id):
        return None
    user = await users.get_user({"_id": ObjectId(user_id)})
    if not user:
        return None
    if hasattr(user, "model_dump"):
        d = user.model_dump(by_alias=True)
    else:
        d = user.dict(by_alias=True)
    if d.get("id") is not None:
        d["id"] = str(d["id"])
    if d.get("_id") is not None:
        d["_id"] = str(d["_id"])
    return d


async def user_set_stripe_customer_id(users: UserModel, user_id: str, stripe_customer_id: str) -> None:
    await users.update_user(user_id, {"stripe_customer_id": stripe_customer_id})


def subscription_payload_for_plan(plan_name: Optional[str]) -> dict[str, Any]:
    """Build users.subscription object for Solo / Core / Pro (or free)."""
    name = (plan_name or "").strip()
    plan_key = name.lower() if name else "free"
    if plan_key not in ("solo", "core", "pro"):
        plan_key = "free"
    taken = plan_key != "free"
    return {
        "isSubscriptionTaken": taken,
        "subscriptionType": plan_key,
        "subscribedAt": datetime.utcnow() if taken else None,
    }


async def sync_user_subscription_plan(
    users: UserModel, user_id: str, plan_name: Optional[str]
) -> None:
    await users.update_user(
        user_id,
        {
            "subscription": subscription_payload_for_plan(plan_name),
            "updatedOn": datetime.utcnow(),
        },
    )


def get_subscription_entitlements(product_name: str) -> dict[str, Any]:
    """Plan limits and feature flags keyed by catalog name (Solo / Core / Pro)."""
    by_name: dict[str, dict[str, Any]] = {
        "Solo": {
            "speakerProfiles": 1,
            "speakerProfilesMin": 1,
            "speakerProfilesMax": 1,
            "opportunities": None,
            "opportunityIdentification": True,
            "packagedSpeaker": False,
            "firesideChat": False,
        },
        "Core": {
            "speakerProfiles": 4,
            "speakerProfilesMin": 2,
            "speakerProfilesMax": 4,
            "opportunities": None,
            "opportunityIdentification": True,
            "packagedSpeaker": True,
            "firesideChat": False,
        },
        "Pro": {
            "speakerProfiles": 10,
            "speakerProfilesMin": 5,
            "speakerProfilesMax": 10,
            "opportunities": None,
            "opportunityIdentification": True,
            "packagedSpeaker": True,
            "firesideChat": True,
        },
    }
    return dict(by_name.get(product_name, _inactive_entitlements()))


def _inactive_entitlements() -> dict[str, Any]:
    return {
        "speakerProfiles": 0,
        "speakerProfilesMin": 0,
        "speakerProfilesMax": 0,
        "opportunities": 0,
        "opportunityIdentification": False,
        "packagedSpeaker": False,
        "firesideChat": False,
    }


def plan_limits_from_entitlements(ent: dict[str, Any]) -> dict[str, Any]:
    return {
        "speakerProfiles": ent.get("speakerProfilesMax") or ent.get("speakerProfiles"),
        "speakerProfilesMin": ent.get("speakerProfilesMin"),
        "speakerProfilesMax": ent.get("speakerProfilesMax"),
        "opportunities": ent.get("opportunities"),
    }


def plan_features_from_entitlements(ent: dict[str, Any]) -> dict[str, bool]:
    return {
        "opportunityIdentification": bool(ent.get("opportunityIdentification")),
        "packagedSpeaker": bool(ent.get("packagedSpeaker")),
        "firesideChat": bool(ent.get("firesideChat")),
    }


def entitlements_for_mongo(ent: dict[str, Any]) -> dict[str, Any]:
    """Fields persisted on subscription documents."""
    return {
        "speakerProfiles": ent.get("speakerProfilesMax") or ent.get("speakerProfiles") or 0,
        "speakerProfilesMin": ent.get("speakerProfilesMin"),
        "speakerProfilesMax": ent.get("speakerProfilesMax"),
        "opportunities": ent.get("opportunities"),
        "opportunityIdentification": ent.get("opportunityIdentification"),
        "packagedSpeaker": ent.get("packagedSpeaker"),
        "firesideChat": ent.get("firesideChat"),
    }


def _as_optional_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def compute_subscription_fields(
    stripe_subscription: Mapping[str, Any],
    cancel_at_period_end: bool,
    cancel_at: Any,
    stripe_status: str,
) -> dict[str, Any]:
    items = stripe_subscription.get("items") or {}
    data = (items.get("data") or [{}])[0] if items else {}

    current_period_end = _as_optional_int(stripe_subscription.get("current_period_end"))
    if current_period_end is None:
        current_period_end = _as_optional_int(data.get("current_period_end"))

    next_billing_date = current_period_end

    cancellation_date: Optional[int] = None
    cancel_at_int = _as_optional_int(cancel_at)
    if cancel_at_int is not None:
        cancellation_date = cancel_at_int
    elif cancel_at_period_end and current_period_end is not None:
        cancellation_date = current_period_end

    is_scheduled_to_cancel = cancel_at_int is not None
    is_cancelled = stripe_status in ("canceled", "unpaid", "past_due")

    return {
        "next_billing_date": next_billing_date,
        "cancellation_date": cancellation_date,
        "is_scheduled_to_cancel": is_scheduled_to_cancel,
        "is_cancelled": is_cancelled,
    }


# Current monthly products plus legacy annual ids mapped for existing Stripe subscriptions.
STRIPE_PRODUCT_PLAN_NAME: dict[str, str] = {
    "prod_UL4xCeKLlV8bem": "Solo",
    "prod_UL4yDwRO0O9bIJ": "Core",
    "prod_UL4yIM1YBucxc5": "Pro",
    "prod_UL4zkiJieRAZ20": "Solo",
    "prod_UL4zVwBSOsvUyH": "Core",
    "prod_UL502HhmkXfZZB": "Pro",
}


def plan_name_from_stripe_product_id(product_id: Optional[str]) -> Optional[str]:
    if not product_id:
        return None
    return STRIPE_PRODUCT_PLAN_NAME.get(str(product_id))


def tier_from_stripe_product_id(product_id: Optional[str]) -> Optional[str]:
    return plan_name_from_stripe_product_id(product_id)


def select_primary_subscription(subscriptions: List[Any]) -> Optional[Any]:
    if not subscriptions:
        return None
    for sub in subscriptions:
        sd = as_dict(sub)
        if sd.get("status") in ("active", "trialing"):
            return sub
    return subscriptions[0]


def plan_product_id_from_subscription(sub: Any) -> Optional[str]:
    sd = as_dict(sub)

    plan = sd.get("plan")
    if plan:
        pd = as_dict(plan)
        prod = pd.get("product")
        if isinstance(prod, dict):
            prod = prod.get("id")
        if prod:
            return str(prod)

    items = sd.get("items") or {}
    data = items.get("data") or []
    if not data:
        return None
    first = as_dict(data[0])
    price = first.get("price") or {}
    if not isinstance(price, dict):
        price = as_dict(price)
    prod = price.get("product")
    if isinstance(prod, dict):
        prod = prod.get("id")
    return str(prod) if prod else None


def _subscription_period_field(sd: Mapping[str, Any], field: str) -> Any:
    items = sd.get("items") or {}
    row = (items.get("data") or [{}])[0] if items else {}
    return sd.get(field) or row.get(field)


def subscription_timestamp_iso_fields(
    stripe_sub: Optional[Mapping[str, Any]],
    mongo_sub: Optional[Mapping[str, Any]] = None,
) -> dict[str, Optional[str]]:
    keys = (
        "expireTime",
        "purchase_date_ms",
        "current_period_start",
        "current_period_end",
        "billing_cycle_anchor",
        "cancel_at",
        "canceled_at",
        "ended_at",
        "next_billing_date",
        "cancellation_date",
    )
    out: dict[str, Optional[str]] = {k: None for k in keys}

    if mongo_sub:
        pm = mongo_sub.get("purchase_date_ms")
        if pm is not None and str(pm).strip() != "":
            out["purchase_date_ms"] = stripe_timestamp_to_iso(pm)

    if not stripe_sub:
        return out

    sd = dict(as_dict(stripe_sub))

    trial_end = sd.get("trial_end")
    expire_raw = trial_end if trial_end is not None else _subscription_period_field(sd, "current_period_end")
    out["expireTime"] = stripe_timestamp_to_iso(expire_raw)

    if out["purchase_date_ms"] is None:
        created = sd.get("created")
        if created is not None:
            out["purchase_date_ms"] = stripe_timestamp_to_iso(int(created) * 1000)

    out["current_period_start"] = stripe_timestamp_to_iso(
        _subscription_period_field(sd, "current_period_start")
    )
    out["current_period_end"] = stripe_timestamp_to_iso(
        _subscription_period_field(sd, "current_period_end")
    )
    out["billing_cycle_anchor"] = stripe_timestamp_to_iso(sd.get("billing_cycle_anchor"))
    out["cancel_at"] = stripe_timestamp_to_iso(sd.get("cancel_at"))
    out["canceled_at"] = stripe_timestamp_to_iso(sd.get("canceled_at"))
    out["ended_at"] = stripe_timestamp_to_iso(sd.get("ended_at"))

    computed = compute_subscription_fields(
        sd,
        bool(sd.get("cancel_at_period_end")),
        sd.get("cancel_at"),
        str(sd.get("status") or ""),
    )
    out["next_billing_date"] = stripe_timestamp_to_iso(computed.get("next_billing_date"))
    out["cancellation_date"] = stripe_timestamp_to_iso(computed.get("cancellation_date"))

    return out


MONGO_SUBSCRIPTION_TIMESTAMP_FIELDS = (
    "expireTime",
    "purchase_date_ms",
    "current_period_start",
    "current_period_end",
    "billing_cycle_anchor",
    "cancel_at",
    "canceled_at",
    "ended_at",
    "next_billing_date",
    "cancellation_date",
)


def _json_safe_mongo_value(value: Any) -> Any:
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt.isoformat()
    if isinstance(value, dict):
        return {str(k): _json_safe_mongo_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe_mongo_value(v) for v in value]
    return value


def subscription_active_from_mongo_doc(doc: Mapping[str, Any]) -> bool:
    is_active = bool(doc.get("active"))
    et = doc.get("expireTime")
    if et is not None and str(et).strip() != "":
        ms = node_style_timestamp_ms(et)
        if ms is not None:
            is_active = time.time() * 1000 < ms
    return is_active


def process_mongo_subscription_document(doc: Mapping[str, Any]) -> dict[str, Any]:
    out = _json_safe_mongo_value(dict(doc))
    for key in MONGO_SUBSCRIPTION_TIMESTAMP_FIELDS:
        out[key] = stripe_timestamp_to_iso(doc.get(key))
    out["active"] = subscription_active_from_mongo_doc(doc)
    return out


def process_mongo_subscriptions_for_api(docs: List[Mapping[str, Any]]) -> List[dict[str, Any]]:
    return [process_mongo_subscription_document(d) for d in docs]
