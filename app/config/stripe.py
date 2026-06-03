from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, Optional


@dataclass(frozen=True)
class ProductConfig:
    id: str
    price_id: Optional[str]
    name: str
    price: float
    interval: str


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    v = os.environ.get(name)
    return v if v is not None and v != "" else default


# Temporary catalog — ProductConfig.name is tierKey (Solo / Core / Pro), not Stripe display name.
# USD list price (dollars). price_id None → payment-link flow resolves Price from Stripe by product id.
STRIPE_PRODUCTS: List[ProductConfig] = [
    ProductConfig("prod_UdOMBnQAaLpDg0", None, "Solo", 49.0, "monthly"),
    ProductConfig("prod_UdONfDl80QUCYy", None, "Core", 149.0, "monthly"),
    ProductConfig("prod_UdOOBO0LKnlazQ", None, "Pro", 499.0, "monthly"),
    ProductConfig("prod_UdOMcsNT3mELYo", None, "Solo", 490.0, "yearly"),
    ProductConfig("prod_UdOO1uatwnegWM", None, "Core", 1490.0, "yearly"),
    ProductConfig("prod_UdOPt0rqJPIsEB", None, "Pro", 4990.0, "yearly"),
]

STRIPE_PRODUCT_IDS: frozenset[str] = frozenset(p.id for p in STRIPE_PRODUCTS)


def get_products() -> List[ProductConfig]:
    return list(STRIPE_PRODUCTS)


@dataclass(frozen=True)
class StripeSettings:
    secret_key: str
    webhook_secret: Optional[str]
    publishable_key: Optional[str]
    success_url: Optional[str]
    success_url_subscription: Optional[str]

    @classmethod
    def from_env(cls) -> StripeSettings:
        """Stripe keys and URLs come from environment / .env only (not app.config.Settings)."""
        secret = _env("STRIPE_SECRET_KEY")
        if not secret:
            raise RuntimeError("STRIPE_SECRET_KEY is required")
        return cls(
            secret_key=secret,
            webhook_secret=_env("STRIPE_WEBHOOK_KEY"),
            publishable_key=_env("STRIPE_PUBLISHABLE_KEY"),
            success_url=_env("STRIPE_SUCCESS_URL"),
            success_url_subscription=_env("STRIPE_SUCCESS_URL_SUBSCRIPTION"),
        )
