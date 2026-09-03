from __future__ import annotations

import logging
import stripe
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.config.stripe import StripeSettings
from app.dependencies import get_speaker_profile_model, get_subscription_service
from app.models.SpeakerProfile import SpeakerProfileModel
from app.helpers.Utilities import Utils
from app.middleware.JWTVerification import jwt_validator
from app.schemas.ServerResponse import ServerResponse
from app.schemas.Subscriptions import BillingPortalRequest, CreatePaymentLinkRequest
from app.services.Subscriptions import SubscriptionsService

logger = logging.getLogger(__name__)

public_router = APIRouter(prefix="/api/v1/subscriptions", tags=["subscriptions"])
auth_router = APIRouter(prefix="/api/v1/subscriptions", tags=["subscriptions"])


def _user_id_from_jwt(payload: dict) -> str:
    for key in ("id", "_id"):
        v = payload.get(key)
        if v is not None and str(v).strip() != "":
            return str(v)
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"data": None, "error": "Missing user id in token", "success": False},
    )


def _require_stripe_settings() -> StripeSettings:
    try:
        return StripeSettings.from_env()
    except RuntimeError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"data": None, "error": str(e), "success": False},
        ) from e


@public_router.post("/webhook")
async def stripe_webhook(
    request: Request,
    service: SubscriptionsService = Depends(get_subscription_service),
):
    payload = await request.body()
    sig = request.headers.get("stripe-signature")
    body, code = await service.handle_webhook(payload, sig)
    return JSONResponse(content=body, status_code=code)


@auth_router.post("/create-payment-link", response_model=ServerResponse)
async def create_payment_link(
    body: CreatePaymentLinkRequest,
    jwt_payload: dict = Depends(jwt_validator),
    service: SubscriptionsService = Depends(get_subscription_service),
):
    try:
        _require_stripe_settings()
        uid = _user_id_from_jwt(jwt_payload)
        result = await service.create_payment_link(
            user_id=uid,
            product_id=body.productId,
            userflow=body.userflow,
            cancel_url=body.cancelUrl,
        )
        if result.status >= 400:
            raise HTTPException(
                status_code=result.status,
                detail={
                    "data": None,
                    "error": result.message,
                    "success": False,
                },
            )
        return Utils.create_response(
            {
                "paymentLinkUrl": result.payment_link_url,
                "paymentLinkId": result.payment_link_id,
                "subscriptionUpdated": result.subscription_updated,
                "message": result.message,
            },
            True,
            "",
        )
    except HTTPException:
        raise
    except ValidationError as ve:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"data": None, "error": str(ve), "success": False},
        ) from ve
    except Exception as e:
        logger.exception("create-payment-link failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"data": None, "error": "Internal server error", "success": False},
        ) from e


@auth_router.post("/billing-portal", response_model=ServerResponse)
async def billing_portal(
    body: BillingPortalRequest,
    jwt_payload: dict = Depends(jwt_validator),
    service: SubscriptionsService = Depends(get_subscription_service),
):
    settings = _require_stripe_settings()
    uid = _user_id_from_jwt(jwt_payload)
    user = await service.fetch_user(uid)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"data": None, "error": "User not found", "success": False},
        )
    cid = user.get("stripe_customer_id")
    if not cid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"data": None, "error": "No Stripe customer on file", "success": False},
        )
    try:
        out = service.billing_portal(
            stripe_customer_id=str(cid),
            return_url=body.returnUrl,
            settings=settings,
        )
        return Utils.create_response(out, True, "")
    except stripe.error.StripeError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"data": None, "error": str(e), "success": False},
        ) from e


@auth_router.get("/invoices", response_model=ServerResponse)
async def list_my_stripe_invoices(
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    limit: int = Query(10, ge=1, le=100, description="Items per page"),
    jwt_payload: dict = Depends(jwt_validator),
    service: SubscriptionsService = Depends(get_subscription_service),
):
    """List Stripe invoices for the logged-in user (by ``stripe_customer_id`` on the user)."""
    _require_stripe_settings()
    uid = _user_id_from_jwt(jwt_payload)
    user = await service.fetch_user(uid)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"data": None, "error": "User not found", "success": False},
        )
    cid = user.get("stripe_customer_id")
    if not cid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"data": None, "error": "No Stripe customer on file", "success": False},
        )
    try:
        data = service.list_invoices(stripe_customer_id=str(cid), page=page, limit=limit)
        return Utils.create_response(data, True, "")
    except stripe.error.StripeError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"data": None, "error": str(e), "success": False},
        ) from e


@auth_router.get("/current-subscription", response_model=ServerResponse)
async def current_subscription_from_stripe(
    jwt_payload: dict = Depends(jwt_validator),
    service: SubscriptionsService = Depends(get_subscription_service),
    speaker_profiles: SpeakerProfileModel = Depends(get_speaker_profile_model),
):
    uid = _user_id_from_jwt(jwt_payload)
    empty = {
        "hasActiveSubscription": False,
        "planName": None,
        "planLimits": None,
        "planFeatures": None,
        "planUsage": {"speakerProfiles": 0, "opportunities": None},
        "stripeProductId": None,
    }
    try:
        _require_stripe_settings()
        payload = await service.current_subscription_payload(
            user_id=uid,
            speaker_profiles=speaker_profiles,
        )
        return Utils.create_response(payload, True, "")
    except LookupError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"data": None, "error": str(e), "success": False},
        ) from e
    except Exception as e:
        logger.warning("current-subscription falling back to no plan: %s", e)
        return Utils.create_response(empty, True, "")
