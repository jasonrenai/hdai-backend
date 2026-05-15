from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, List, Optional, Tuple

import stripe

from app.config.stripe import ProductConfig, StripeSettings, get_products
from app.helpers.SubscriptionStripeUtil import (
    as_dict,
    compute_subscription_fields,
    entitlements_for_mongo,
    get_subscription_entitlements,
    user_set_stripe_customer_id,
)
from app.models.Subscriptions import SubscriptionsModel
from app.models.User import UserModel

logger = logging.getLogger(__name__)


@dataclass
class WebhookContext:
    users: UserModel
    subscriptions: SubscriptionsModel
    products: List[ProductConfig]


def _resolve_user_id_from_checkout_session(session: dict[str, Any], ctx: WebhookContext) -> Optional[str]:
    meta = session.get("metadata") or {}
    user_id = meta.get("userId")
    if user_id:
        return str(user_id)

    payment_link_id = session.get("payment_link")
    if payment_link_id:
        try:
            pl = stripe.PaymentLink.retrieve(payment_link_id)
            pld = as_dict(pl)
            m = pld.get("metadata") or {}
            if m.get("userId"):
                return str(m["userId"])
        except stripe.error.StripeError as e:
            logger.error("Error retrieving payment link: %s", e)

    customer_id = session.get("customer")
    if customer_id:
        try:
            cust = stripe.Customer.retrieve(customer_id)
            cd = as_dict(cust)
            m = cd.get("metadata") or {}
            if m.get("userId"):
                return str(m["userId"])
        except stripe.error.StripeError as e:
            logger.error("Error retrieving customer: %s", e)

    return None


def _find_product_for_line_item(
    products: List[ProductConfig], price_id: Optional[str], product_id: Optional[str]
) -> Optional[ProductConfig]:
    if price_id:
        for p in products:
            if p.price_id == price_id:
                return p
    if product_id:
        for p in products:
            if p.id == product_id:
                return p
    return None


def _first_line_item_price_product(session_id: str) -> Tuple[Optional[str], Optional[str]]:
    items = stripe.checkout.Session.list_line_items(session_id, expand=["data.price.product"])
    data = list(getattr(items, "data", None) or as_dict(items).get("data") or [])
    if not data:
        return None, None
    li = as_dict(data[0])
    price = (li or {}).get("price") or {}
    if not isinstance(price, dict):
        price = as_dict(price)
    price_id = price.get("id")
    prod = price.get("product")
    if isinstance(prod, dict):
        product_id = prod.get("id")
    else:
        product_id = prod
    return price_id, str(product_id) if product_id else None


def _fill_billing_from_subscription(sub: dict[str, Any]) -> dict[str, Any]:
    items = sub.get("items") or {}
    row = (items.get("data") or [{}])[0] if items else {}

    out: dict[str, Any] = {}
    cps = sub.get("current_period_start") or row.get("current_period_start")
    cpe = sub.get("current_period_end") or row.get("current_period_end")
    if cps is not None:
        out["current_period_start"] = str(cps)
    if cpe is not None:
        out["current_period_end"] = str(cpe)
    bca = sub.get("billing_cycle_anchor")
    if bca is not None:
        out["billing_cycle_anchor"] = str(bca)
    if sub.get("cancel_at_period_end") is not None:
        out["cancel_at_period_end"] = sub.get("cancel_at_period_end")
    if sub.get("cancel_at") is not None:
        out["cancel_at"] = str(sub.get("cancel_at"))
    out["stripe_status"] = sub.get("status")
    return out


async def handle_checkout_session_completed(session_obj: Any, ctx: WebhookContext) -> None:
    session = as_dict(session_obj)
    logger.info("Checkout session completed: %s", session.get("id"))

    user_id = _resolve_user_id_from_checkout_session(session, ctx)
    if not user_id:
        logger.error("No userId found in session, payment link, or customer metadata")
        return

    price_id, product_id_from_line = _first_line_item_price_product(str(session["id"]))
    product = _find_product_for_line_item(ctx.products, price_id, product_id_from_line)
    if not product:
        logger.error("Product not found for priceId=%s productId=%s", price_id, product_id_from_line)
        return

    ent = entitlements_for_mongo(get_subscription_entitlements(product.name))
    stripe_customer_id = session.get("customer")
    if not stripe_customer_id:
        logger.error("Checkout session missing customer")
        return

    stripe_subscription_id: Optional[str] = None
    billing_period_data: dict[str, Any] = {}
    stripe_subscription: Optional[dict[str, Any]] = None

    if session.get("subscription"):
        stripe_subscription_id = str(session["subscription"])
        try:
            sub = stripe.Subscription.retrieve(stripe_subscription_id)
            stripe_subscription = dict(as_dict(sub))
            billing_period_data = _fill_billing_from_subscription(stripe_subscription)
        except stripe.error.StripeError as e:
            logger.error("Error retrieving subscription from Stripe: %s", e)
    else:
        try:
            subs = stripe.Subscription.list(customer=stripe_customer_id, status="active", limit=1)
            if subs.data:
                stripe_subscription = as_dict(subs.data[0])
                stripe_subscription_id = str(stripe_subscription.get("id") or "")
                billing_period_data = _fill_billing_from_subscription(stripe_subscription)
        except stripe.error.StripeError as e:
            logger.error("Error retrieving subscriptions from Stripe: %s", e)

    try:
        await user_set_stripe_customer_id(ctx.users, user_id, str(stripe_customer_id))
    except Exception as e:
        logger.error("Failed to update user %s with Stripe customer ID: %s", user_id, e)

    existing = await ctx.subscriptions.find_by_user_id(user_id)

    computed: dict[str, Any] = {}
    if stripe_subscription and billing_period_data.get("stripe_status"):
        computed = compute_subscription_fields(
            stripe_subscription,
            bool(billing_period_data.get("cancel_at_period_end")),
            billing_period_data.get("cancel_at"),
            str(billing_period_data.get("stripe_status") or ""),
        )

    purchase_ms = str(int(time.time() * 1000))

    common_set = {
        "active": True,
        "success": True,
        "productId": product.id,
        "subscription_price": str(product.price),
        "purchase_date_ms": purchase_ms,
        "stripe_customer_id": str(stripe_customer_id),
        "stripe_subscription_id": stripe_subscription_id,
        **ent,
        **billing_period_data,
        **computed,
    }

    if not existing:
        await ctx.subscriptions.insert_one(
            {
                "user_id": user_id,
                "interval": product.interval or "monthly",
                "subscription_type": product.name,
                **ent,
            }
        )
        await ctx.subscriptions.update_by_user_id(user_id, common_set)
    else:
        await ctx.subscriptions.update_by_user_id(
            user_id,
            {
                "user_id": user_id,
                "subscription_type": product.name,
                "interval": product.interval or "monthly",
                **common_set,
            },
        )

    logger.info("Subscription upserted for user %s product=%s", user_id, product.name)


async def handle_async_payment_succeeded(session_obj: Any, ctx: WebhookContext) -> None:
    session = as_dict(session_obj)
    logger.info("Async payment succeeded: %s", session.get("id"))
    user_id = _resolve_user_id_from_checkout_session(session, ctx)
    if not user_id:
        logger.error("No userId for async payment success")
        return
    await ctx.subscriptions.update_by_user_id(user_id, {"active": True, "success": True})


async def handle_async_payment_failed(session_obj: Any, ctx: WebhookContext) -> None:
    session = as_dict(session_obj)
    logger.info("Async payment failed: %s", session.get("id"))
    user_id = _resolve_user_id_from_checkout_session(session, ctx)
    if not user_id:
        logger.error("No userId for async payment failure")
        return
    await ctx.subscriptions.update_by_user_id(user_id, {"active": False, "success": False})


def _find_product_from_subscription(
    products: List[ProductConfig], full: dict[str, Any]
) -> Optional[ProductConfig]:
    items = full.get("items") or {}
    data = items.get("data") or []
    if not data:
        return None
    price = (data[0] or {}).get("price") or {}
    price_id = price.get("id")
    product_ref = price.get("product")
    product_id = product_ref.get("id") if isinstance(product_ref, dict) else product_ref
    if price_id:
        for p in products:
            if p.price_id == price_id:
                return p
    if product_id:
        for p in products:
            if p.id == str(product_id):
                return p
    return None


async def handle_subscription_updated(subscription_obj: Any, ctx: WebhookContext) -> None:
    subscription = as_dict(subscription_obj)
    stripe_customer_id = subscription.get("customer")
    stripe_subscription_id = subscription.get("id")

    if not stripe_customer_id:
        logger.error("No customer ID found in subscription object")
        return

    full = subscription
    if stripe_subscription_id:
        try:
            full = as_dict(stripe.Subscription.retrieve(str(stripe_subscription_id)))
            logger.info("Retrieved full subscription from Stripe: %s", stripe_subscription_id)
        except stripe.error.StripeError as e:
            logger.error("Error retrieving subscription from Stripe, using webhook data: %s", e)
            try:
                subs = stripe.Subscription.list(customer=str(stripe_customer_id), status="active", limit=1)
                if subs.data:
                    full = as_dict(subs.data[0])
                    logger.info("Found subscription by customer ID: %s", full.get("id"))
            except stripe.error.StripeError as e2:
                logger.error("Error listing subscriptions by customer ID: %s", e2)

    db_sub = await ctx.subscriptions.find_by_stripe_customer_id(str(stripe_customer_id))
    if not db_sub:
        logger.warning("No subscription found in database for customer %s", stripe_customer_id)
        return

    status = str(full.get("status") or "")
    is_cancelled = status in ("canceled", "unpaid", "past_due")
    is_scheduled_to_cancel = bool(full.get("cancel_at"))
    is_active = status == "active" and not is_cancelled and not is_scheduled_to_cancel

    product = _find_product_from_subscription(ctx.products, full)

    update_data: dict[str, Any] = {
        "active": is_active,
        "success": is_active,
        "stripe_subscription_id": full.get("id"),
        "stripe_status": status,
        "cancel_at_period_end": full.get("cancel_at_period_end") is True,
    }

    items = full.get("items") or {}
    row = (items.get("data") or [{}])[0] if items else {}
    cps = full.get("current_period_start") or row.get("current_period_start")
    cpe = full.get("current_period_end") or row.get("current_period_end")
    if cps is not None:
        update_data["current_period_start"] = cps
    if cpe is not None:
        update_data["current_period_end"] = cpe
    if full.get("billing_cycle_anchor") is not None:
        update_data["billing_cycle_anchor"] = full.get("billing_cycle_anchor")

    product_changed = bool(
        product and db_sub.get("productId") and product.id != db_sub.get("productId")
    )

    if product:
        ent = entitlements_for_mongo(get_subscription_entitlements(product.name))
        update_data["subscription_type"] = product.name
        update_data["productId"] = product.id
        update_data["subscription_price"] = str(product.price)
        update_data["interval"] = product.interval or "monthly"
        update_data.update(ent)

    if product_changed and status == "active":
        update_data["cancel_at"] = None
        update_data["cancel_at_period_end"] = False
        logger.info("Plan changed - clearing cancellation for customer %s", stripe_customer_id)
        is_scheduled_to_cancel = False
    else:
        if full.get("cancel_at") is not None:
            update_data["cancel_at"] = full.get("cancel_at")
        if full.get("cancel_at_period_end") is not None:
            update_data["cancel_at_period_end"] = full.get("cancel_at_period_end") is True

    if full.get("canceled_at") is not None:
        update_data["canceled_at"] = full.get("canceled_at")
    if full.get("ended_at") is not None:
        update_data["ended_at"] = full.get("ended_at")

    final_cancel_at_period_end = bool(update_data.get("cancel_at_period_end"))
    final_cancel_at = (
        update_data["cancel_at"]
        if "cancel_at" in update_data
        else full.get("cancel_at", db_sub.get("cancel_at"))
    )
    final_stripe_status = str(update_data.get("stripe_status") or status)

    computed = compute_subscription_fields(
        full,
        final_cancel_at_period_end,
        final_cancel_at,
        final_stripe_status,
    )
    update_data["next_billing_date"] = computed.get("next_billing_date")
    update_data["cancellation_date"] = computed.get("cancellation_date")
    update_data["is_scheduled_to_cancel"] = computed.get("is_scheduled_to_cancel")
    update_data["is_cancelled"] = computed.get("is_cancelled")

    if is_cancelled:
        inactive = entitlements_for_mongo(get_subscription_entitlements(""))
        update_data["active"] = False
        update_data["success"] = False
        update_data["subscription_type"] = None
        update_data.update(inactive)
        update_data["storage"] = None
        update_data["interval"] = None
        update_data["productId"] = None
        update_data["subscription_price"] = None
        update_data["current_period_start"] = None
        update_data["current_period_end"] = None
        update_data["billing_cycle_anchor"] = None
        update_data["next_billing_date"] = None
        update_data["cancel_at_period_end"] = False
        update_data["cancel_at"] = None
        update_data["is_scheduled_to_cancel"] = False
        logger.info("Subscription cancelled for customer %s - no active plan", stripe_customer_id)
    elif is_scheduled_to_cancel:
        update_data["active"] = True
        update_data["success"] = True
        logger.info("Subscription scheduled to cancel at period end for customer %s", stripe_customer_id)
        if full.get("cancel_at"):
            logger.info(
                "Cancellation date: %s",
                datetime.fromtimestamp(int(full["cancel_at"]), tz=timezone.utc).isoformat(),
            )
    elif is_active or (product_changed and status == "active" and not is_cancelled):
        update_data["active"] = True
        update_data["success"] = True
        if product_changed:
            logger.info("Subscription plan changed and is active for customer %s", stripe_customer_id)
        else:
            logger.info("Subscription active for customer %s", stripe_customer_id)

    await ctx.subscriptions.update_by_stripe_customer_id(str(stripe_customer_id), update_data)
    logger.info("Subscription updated successfully for customer %s", stripe_customer_id)


async def handle_raw_webhook(
    payload: bytes,
    stripe_signature: str | None,
    *,
    users: UserModel,
    subscriptions: SubscriptionsModel,
    settings: StripeSettings | None = None,
) -> Tuple[dict[str, Any], int]:
    settings = settings or StripeSettings.from_env()
    secret = settings.webhook_secret
    if not secret:
        logger.error("STRIPE_WEBHOOK_KEY is not set")
        return {"error": "Webhook secret not configured"}, 500
    if not stripe_signature:
        return {"error": "Missing stripe-signature header"}, 400

    try:
        event = stripe.Webhook.construct_event(payload, stripe_signature, secret)
    except (ValueError, stripe.error.SignatureVerificationError) as e:
        logger.error("Webhook signature verification failed: %s", e)
        return {"error": f"Webhook Error: {e!s}"}, 400

    ctx = WebhookContext(users=users, subscriptions=subscriptions, products=get_products())

    try:
        ed = as_dict(event)
        etype = ed.get("type")
        data_object = (ed.get("data") or {}).get("object")

        if etype == "checkout.session.completed":
            await handle_checkout_session_completed(data_object, ctx)
        elif etype == "checkout.session.async_payment_succeeded":
            await handle_async_payment_succeeded(data_object, ctx)
        elif etype == "checkout.session.async_payment_failed":
            await handle_async_payment_failed(data_object, ctx)
        elif etype == "customer.subscription.updated":
            await handle_subscription_updated(data_object, ctx)
        elif etype == "payment_link.created":
            obj = data_object if isinstance(data_object, dict) else dict(data_object)
            logger.info("Payment link created: %s", obj.get("id"))
        else:
            logger.info("Unhandled event type: %s", etype)

        return {"received": True}, 200
    except Exception as e:
        logger.exception("Error handling webhook: %s", e)
        return {"error": "Error processing webhook"}, 500
