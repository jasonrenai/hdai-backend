"""Welcome email on new account — uses shared EmailService + Postmark welcome template."""

from __future__ import annotations

import logging
from typing import Optional

from app.email.constants import WELCOME_EMAIL_CTA_URL
from app.email.enums import EmailEventType

logger = logging.getLogger(__name__)


def send_welcome_email(
    *,
    user_display_name: str,
    account_email: Optional[str] = None,
) -> bool:
    """Send the configured Postmark welcome template (see EmailEventType.WELCOME_EMAIL)."""
    from app.dependencies import get_email_service

    to_email = (account_email or "").strip()
    if not to_email:
        return False

    user_name = (user_display_name or "").strip() or "there"
    preheader = f"Welcome — signed up as {to_email}"

    return get_email_service().send_event_email(
        event_type=EmailEventType.WELCOME_EMAIL,
        to_email=to_email,
        template_model={
            "preheader": preheader,
            "user_name": user_name,
            "cta_url": WELCOME_EMAIL_CTA_URL,
        },
    )


def try_send_welcome_email_on_account_created(
    *,
    user_display_name: str,
    account_email: Optional[str] = None,
) -> None:
    """Fire-and-forget; never raises."""
    try:
        if not send_welcome_email(
            user_display_name=user_display_name,
            account_email=account_email,
        ):
            logger.warning("Welcome email was not sent (Postmark/template send returned false).")
    except Exception as e:
        logger.warning("Welcome email failed: %s", e)
