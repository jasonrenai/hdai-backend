from __future__ import annotations

import logging
import os
from typing import Any, List, Optional, Tuple

import stripe

from app.config.stripe import ProductConfig, StripeSettings, get_products
from app.helpers.SubscriptionStripeUtil import (
    as_dict,
    compute_subscription_fields,
    entitlements_for_mongo,
    get_subscription_entitlements,
    plan_features_from_entitlements,
    plan_limits_from_entitlements,
    plan_name_from_stripe_product_id,
    plan_product_id_from_subscription,
    process_mongo_subscriptions_for_api,
    select_primary_subscription,
    stripe_timestamp_to_iso,
    subscription_timestamp_iso_fields,
    user_doc_for_stripe,
    user_set_stripe_customer_id,
)
from app.helpers.SubscriptionStripeWebhooks import handle_raw_webhook
from app.models.Subscriptions import SubscriptionsModel
from app.models.User import UserModel
from app.schemas.Subscriptions import PaymentLinkResult

logger = logging.getLogger(__name__)


def _recurring_interval_on_price(price_obj: Any) -> Optional[str]:
    rec = getattr(price_obj, "recurring", None)
    if rec is None and isinstance(price_obj, dict):
        rec = price_obj.get("recurring")
    if not rec:
        return None
    return getattr(rec, "interval", None) if not isinstance(rec, dict) else rec.get("interval")


def _stripe_error_type():
    err_mod = getattr(stripe, "error", None)
    if err_mod is not None and hasattr(err_mod, "StripeError"):
        return err_mod.StripeError
    return getattr(stripe, "StripeError", Exception)


def _stripe_recurring_interval_key(interval: Optional[str]) -> str:
    if interval in ("yearly", "year", "annual"):
        return "year"
    return "month"


def _resolve_price_id_for_product(
    stripe_product_id: str, billing_interval: Optional[str] = None
) -> str:
    prices = stripe.Price.list(product=stripe_product_id, active=True, limit=100)
    if not prices.data:
        raise ValueError(f"No active price found for product {stripe_product_id}")
    desired = _stripe_recurring_interval_key(billing_interval)
    matched = next(
        (p for p in prices.data if _recurring_interval_on_price(p) == desired),
        None,
    )
    chosen = matched or prices.data[0]
    return str(chosen.id)


def _list_subscriptions_for_customer(
    stripe_customer_id: str,
    *,
    status: Optional[str] = None,
    limit: int = 10,
) -> List[Any]:
    params: dict[str, Any] = {"customer": stripe_customer_id, "limit": limit}
    if status:
        params["status"] = status
    result = stripe.Subscription.list(**params)
    return list(result.data)


def _invoice_to_row(inv: Any) -> dict[str, Any]:
    d = as_dict(inv)
    created = d.get("created")
    return {
        "id": d.get("id"),
        "amount_paid": d.get("amount_paid"),
        "currency": d.get("currency"),
        "status": d.get("status"),
        "invoice_pdf": d.get("invoice_pdf"),
        "hosted_invoice_url": d.get("hosted_invoice_url"),
        "created": stripe_timestamp_to_iso(created) if created is not None else None,
    }


def _invoice_cursor_after_skipping(
    stripe_customer_id: str, *, to_skip: int, starting_after: Optional[str]
) -> Optional[str]:
    if to_skip <= 0:
        return None
    cursor: Optional[str] = starting_after
    remaining = to_skip
    while remaining > 0:
        fetch_n = min(100, remaining)
        result = stripe.Invoice.list(
            customer=stripe_customer_id,
            limit=fetch_n,
            starting_after=cursor,
        )
        batch: List[Any] = list(result.data)
        if not batch:
            return None
        if len(batch) < fetch_n:
            return None
        cursor = str(batch[-1].id)
        remaining -= len(batch)
    return cursor


def _list_customer_invoices_page(
    stripe_customer_id: str,
    *,
    page: int,
    limit: int,
) -> dict[str, Any]:
    if page < 1 or limit < 1:
        return {
            "invoices": [],
            "page": max(page, 1),
            "limit": max(limit, 1),
            "hasMore": False,
            "total": 0,
            "totalPages": 0,
        }
    skip = (page - 1) * limit
    cursor = _invoice_cursor_after_skipping(stripe_customer_id, to_skip=skip, starting_after=None)
    if skip > 0 and cursor is None:
        return {
            "invoices": [],
            "page": page,
            "limit": limit,
            "hasMore": False,
            "total": 0,
            "totalPages": 0,
        }
    result = stripe.Invoice.list(
        customer=stripe_customer_id,
        limit=limit,
        starting_after=cursor,
    )
    rows = [_invoice_to_row(inv) for inv in list(result.data)]
    has_more = bool(getattr(result, "has_more", False))
    if has_more:
        return {
            "invoices": rows,
            "page": page,
            "limit": limit,
            "hasMore": True,
            "total": None,
            "totalPages": None,
        }
    total = skip + len(rows)
    total_pages = (total + limit - 1) // limit if limit else 0
    return {
        "invoices": rows,
        "page": page,
        "limit": limit,
        "hasMore": False,
        "total": total,
        "totalPages": total_pages,
    }


def _customer_id_from_subscription(sub: Any) -> Optional[str]:
    d = as_dict(sub)
    c = d.get("customer")
    if isinstance(c, dict):
        return c.get("id")
    return str(c) if c else None


def _subscription_item_and_price(sub: Any) -> tuple[Optional[str], Optional[str]]:
    d = as_dict(sub)
    items = d.get("items") or {}
    data: List[dict] = list(items.get("data") or [])
    if not data:
        return None, None
    first = data[0]
    return first.get("id"), (first.get("price") or {}).get("id")


async def _customer_id_for_checkout(
    *,
    user: dict[str, Any],
    user_id: str,
    product: ProductConfig,
    users: UserModel,
) -> str:
    metadata = {
        "userId": str(user_id),
        "productId": product.id,
        "productName": product.name,
    }
    customer_id = str(user.get("stripe_customer_id") or "").strip()
    email = str(user.get("email") or "").strip() or None
    name = str(user.get("fullName") or "").strip() or None
    customer_fields = {"metadata": metadata}
    if email:
        customer_fields["email"] = email
    if name:
        customer_fields["name"] = name

    if customer_id:
        try:
            customer = stripe.Customer.retrieve(customer_id)
            if as_dict(customer).get("deleted"):
                customer_id = ""
            else:
                stripe.Customer.modify(customer_id, **customer_fields)
                return customer_id
        except stripe.error.StripeError as e:
            logger.warning("Could not retrieve Stripe customer %s: %s", customer_id, e)

    customer = stripe.Customer.create(**customer_fields)
    customer_id = str(as_dict(customer).get("id") or "")
    if customer_id:
        await user_set_stripe_customer_id(users, user_id, customer_id)
    return customer_id


async def create_stripe_payment_link(
    *,
    user_id: str,
    product_id: str,
    userflow: str,
    cancel_url: Optional[str],
    users: UserModel,
    subscriptions: SubscriptionsModel,
    products: Optional[List[ProductConfig]] = None,
    settings: Optional[StripeSettings] = None,
) -> PaymentLinkResult:
    settings = settings or StripeSettings.from_env()
    products = products or get_products()

    product = next((p for p in products if p.id == product_id or p.price_id == product_id), None)
    if not product:
        return PaymentLinkResult(status=400, message="Invalid product ID")

    user = await user_doc_for_stripe(users, user_id)
    if not user:
        return PaymentLinkResult(status=404, message="User not found")

    price_id = product.price_id
    if not price_id:
        try:
            price_id = _resolve_price_id_for_product(product.id, product.interval)
        except ValueError as e:
            return PaymentLinkResult(status=400, message=str(e))
        except stripe.error.StripeError:
            return PaymentLinkResult(status=500, message="Error fetching product price", payment_link_url=None)

    if userflow == "subscription":
        existing = await subscriptions.find_active_with_stripe(user_id)
        sid = (existing or {}).get("stripe_subscription_id") if existing else None
        if existing and sid:
            try:
                stripe_sub = stripe.Subscription.retrieve(sid)
                status = as_dict(stripe_sub).get("status")
                if status in ("canceled", "unpaid"):
                    pass
                else:
                    item_id, current_price_id = _subscription_item_and_price(stripe_sub)
                    if not item_id:
                        logger.warning(
                            "No subscription item found for user %s; creating payment link",
                            user_id,
                        )
                    else:
                        if current_price_id == price_id:
                            return PaymentLinkResult(
                                status=200,
                                message="Already subscribed to this plan",
                                payment_link_url=settings.success_url_subscription
                                or settings.success_url,
                                payment_link_id=None,
                                subscription_updated=True,
                            )
                        updated = stripe.Subscription.modify(
                            sid,
                            items=[{"id": item_id, "price": price_id}],
                            proration_behavior="always_invoice",
                            metadata={
                                "userId": str(user_id),
                                "productId": product.id,
                                "productName": product.name,
                                "updated_via": "subscription_change",
                            },
                        )
                        ud = as_dict(updated)
                        ent = get_subscription_entitlements(product.name)
                        mongo_ent = entitlements_for_mongo(ent)
                        computed = compute_subscription_fields(
                            ud,
                            bool(ud.get("cancel_at_period_end")),
                            ud.get("cancel_at"),
                            str(ud.get("status") or ""),
                        )
                        stripe_customer_id = _customer_id_from_subscription(updated)
                        await subscriptions.update_by_user_id(
                            user_id,
                            {
                                "subscription_type": product.name,
                                "interval": product.interval or "monthly",
                                **mongo_ent,
                                "productId": product.id,
                                "subscription_price": str(product.price),
                                "stripe_customer_id": stripe_customer_id,
                                "stripe_subscription_id": ud.get("id"),
                                "stripe_status": ud.get("status"),
                                "current_period_start": str(ud.get("current_period_start") or "")
                                if ud.get("current_period_start") is not None
                                else None,
                                "current_period_end": str(ud.get("current_period_end") or "")
                                if ud.get("current_period_end") is not None
                                else None,
                                "billing_cycle_anchor": str(ud.get("billing_cycle_anchor") or "")
                                if ud.get("billing_cycle_anchor") is not None
                                else None,
                                "cancel_at_period_end": bool(ud.get("cancel_at_period_end")),
                                "cancel_at": str(ud["cancel_at"])
                                if ud.get("cancel_at") is not None
                                else None,
                                **computed,
                                "active": ud.get("status") == "active",
                                "success": ud.get("status") == "active",
                            },
                        )
                        if stripe_customer_id:
                            await user_set_stripe_customer_id(users, user_id, stripe_customer_id)
                        return PaymentLinkResult(
                            status=200,
                            message="Subscription updated successfully",
                            payment_link_url=settings.success_url_subscription
                            or settings.success_url,
                            payment_link_id=None,
                            subscription_updated=True,
                        )
            except stripe.error.StripeError as e:
                logger.warning("Error updating existing subscription: %s", e)

    success_url = (
        settings.success_url
        if userflow == "registration"
        else settings.success_url_subscription or settings.success_url
    )
    if not success_url:
        return PaymentLinkResult(status=500, message="STRIPE_SUCCESS_URL(S) not configured")

    metadata = {
        "userId": str(user_id),
        "productId": product.id,
        "productName": product.name,
    }
    customer_id = await _customer_id_for_checkout(
        user=user,
        user_id=user_id,
        product=product,
        users=users,
    )
    if not customer_id:
        return PaymentLinkResult(status=500, message="Error creating Stripe customer")

    try:
        session = stripe.checkout.Session.create(
            mode="subscription",
            line_items=[{"price": price_id, "quantity": 1}],
            customer=customer_id,
            client_reference_id=str(user_id),
            metadata=metadata,
            subscription_data={"metadata": metadata},
            success_url=success_url,
            cancel_url=cancel_url or success_url,
        )
    except _stripe_error_type() as e:
        logger.warning("Checkout Session.create failed: %s", e)
        return PaymentLinkResult(status=502, message=str(e))
    session_data = as_dict(session)
    return PaymentLinkResult(
        status=200,
        message="Checkout session created successfully",
        payment_link_url=session_data.get("url"),
        payment_link_id=session_data.get("id"),
        subscription_updated=False,
    )


def init_stripe_from_env() -> Optional[StripeSettings]:
    key = os.environ.get("STRIPE_SECRET_KEY")
    if not key:
        logger.warning("STRIPE_SECRET_KEY not set; Stripe routes will fail until configured.")
        return None
    settings = StripeSettings.from_env()
    stripe.api_key = settings.secret_key
    return settings


def _subscriptions_with_ts_overrides(
    processed: list[dict[str, Any]], ts: dict[str, Any]
) -> list[dict[str, Any]]:
    if not processed:
        return processed
    return [{**sub, **ts} for sub in processed]


def _subscription_at_root(
    processed: list[dict[str, Any]], ts: dict[str, Any]
) -> dict[str, Any]:
    merged = _subscriptions_with_ts_overrides(processed, ts)
    return dict(merged[0]) if merged else {}


class SubscriptionsService:
    """Stripe-backed subscriptions and billing (API entrypoint)."""

    def __init__(self) -> None:
        self._users = UserModel()
        self._subscriptions = SubscriptionsModel()

    async def fetch_user(self, user_id: str) -> Optional[dict[str, Any]]:
        return await user_doc_for_stripe(self._users, user_id)

    async def handle_webhook(
        self, payload: bytes, stripe_signature: str | None
    ) -> Tuple[dict[str, Any], int]:
        return await handle_raw_webhook(
            payload, stripe_signature, users=self._users, subscriptions=self._subscriptions
        )

    async def create_payment_link(
        self,
        *,
        user_id: str,
        product_id: str,
        userflow: str,
        cancel_url: Optional[str],
    ) -> PaymentLinkResult:
        return await create_stripe_payment_link(
            user_id=user_id,
            product_id=product_id,
            userflow=userflow,
            cancel_url=cancel_url,
            users=self._users,
            subscriptions=self._subscriptions,
        )

    def billing_portal(
        self, *, stripe_customer_id: str, return_url: Optional[str], settings: StripeSettings
    ) -> dict[str, Any]:
        url = return_url or settings.success_url
        session = stripe.billing_portal.Session.create(
            customer=stripe_customer_id, return_url=url
        )
        return {"url": session.url, "session_id": session.id}

    def list_invoices(self, *, stripe_customer_id: str, page: int, limit: int) -> dict[str, Any]:
        return _list_customer_invoices_page(stripe_customer_id, page=page, limit=limit)

    async def current_subscription_payload(
        self,
        *,
        user_id: str,
        speaker_profiles: Any,
    ) -> dict[str, Any]:
        plan_usage = {
            "speakerProfiles": await speaker_profiles.count_by_user_id(user_id),
            "opportunities": None,
        }
        user = await user_doc_for_stripe(self._users, user_id)
        if not user:
            raise LookupError("User not found")

        mongo_subs_raw = await self._subscriptions.find_all_by_user_id(user_id)
        if not mongo_subs_raw:
            return {
                "hasActiveSubscription": False,
                "planName": None,
                "planLimits": None,
                "planFeatures": None,
                "planUsage": plan_usage,
                "stripeProductId": None,
            }

        processed_subscriptions = process_mongo_subscriptions_for_api(mongo_subs_raw)
        mongo_sub = mongo_subs_raw[0]
        cid = user.get("stripe_customer_id")
        inactive = {
            "hasActiveSubscription": False,
            "planName": None,
            "planLimits": None,
            "planFeatures": None,
            "planUsage": plan_usage,
            "stripeProductId": None,
        }
        if not cid:
            ts = subscription_timestamp_iso_fields(None, mongo_sub)
            root = _subscription_at_root(processed_subscriptions, ts)
            payload: dict[str, Any] = {**inactive, **root}
            if not root:
                payload.update(ts)
            return payload

        try:
            subs = _list_subscriptions_for_customer(str(cid), status=None, limit=10)
        except _stripe_error_type() as e:
            logger.warning(
                "Stripe customer %s is not usable on this account: %s",
                cid,
                e,
            )
            return inactive
        primary = select_primary_subscription(subs)
        prod_id = plan_product_id_from_subscription(primary) if primary else None
        plan_name = plan_name_from_stripe_product_id(prod_id)
        if not plan_name and mongo_sub.get("subscription_type"):
            plan_name = str(mongo_sub["subscription_type"])
        ent = get_subscription_entitlements(plan_name) if plan_name else None
        is_active = bool(mongo_sub.get("active")) and plan_name is not None
        if primary:
            sd = as_dict(primary)
            is_active = sd.get("status") in ("active", "trialing")
        ts = subscription_timestamp_iso_fields(primary, mongo_sub)
        root = _subscription_at_root(processed_subscriptions, ts)
        payload = {
            **root,
            "hasActiveSubscription": is_active,
            "stripeProductId": prod_id,
            "planName": plan_name if is_active else None,
            "planLimits": plan_limits_from_entitlements(ent) if is_active and ent else None,
            "planFeatures": plan_features_from_entitlements(ent) if is_active and ent else None,
            "planUsage": plan_usage,
        }
        if not root:
            payload.update(ts)
        return payload
