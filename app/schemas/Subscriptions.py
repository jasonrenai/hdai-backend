from typing import Literal, Optional

from pydantic import BaseModel, Field


class CreatePaymentLinkRequest(BaseModel):
    productId: str = Field(
        ...,
        description="Stripe product id for Solo, Core, or Pro (monthly)",
    )
    userflow: Literal["registration", "subscription"] = Field(...)
    cancelUrl: Optional[str] = None


class BillingPortalRequest(BaseModel):
    returnUrl: Optional[str] = None


class PaymentLinkResult(BaseModel):
    """Outcome of ``create_stripe_payment_link`` / ``SubscriptionsService.create_payment_link``."""

    status: int
    message: str
    payment_link_url: Optional[str] = None
    payment_link_id: Optional[str] = None
    subscription_updated: bool = False
