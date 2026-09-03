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
    ProductConfig("prod_VC34SAi6O7Q4gA", "price_1UBeylQvzttp1fSTqAnOw4xv", "Solo", 49.0, "monthly"),
    ProductConfig("prod_VC34zjAKxU2eC8", "price_1UBeymQvzttp1fSTfxoKhZPg", "Core", 149.0, "monthly"),
    ProductConfig("prod_VC34pBUkTTZZnL", "price_1UBeynQvzttp1fSTfRQTzObN", "Pro", 499.0, "monthly"),
    ProductConfig("prod_VC34wOot9slL0L", "price_1UBeypQvzttp1fSTF620zY2K", "Solo", 490.0, "yearly"),
    ProductConfig("prod_VC35Vgv1Aoxp72", "price_1UBeyqQvzttp1fSTeOpjJ5Al", "Core", 1490.0, "yearly"),
    ProductConfig("prod_VC352dAHp8RK8c", "price_1UBeyqQvzttp1fSTr4bJggKn", "Pro", 4990.0, "yearly"),
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
