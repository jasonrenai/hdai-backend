"""Signup welcome + email verification emails (Postmark templates)."""

from __future__ import annotations

import logging
from typing import Optional

from app.email.constants import build_email_verification_url
from app.email.enums import EmailEventType

logger = logging.getLogger(__name__)


def send_signup_welcome_email(*, full_name: str, account_email: str) -> bool:
    from app.dependencies import get_email_service

    to_email = (account_email or "").strip()
    if not to_email:
        return False

    user_name = (full_name or "").strip() or "there"
    return get_email_service().send_event_email(
        event_type=EmailEventType.SIGNUP_WELCOME_EMAIL,
        to_email=to_email,
        template_model={"user_name": user_name},
    )


def send_verify_email_confirmation(*, full_name: str, account_email: str, user_id: str) -> bool:
    from app.dependencies import get_email_service

    to_email = (account_email or "").strip()
    uid = (user_id or "").strip()
    if not to_email or not uid:
        return False

    user_name = (full_name or "").strip() or "there"
    return get_email_service().send_event_email(
        event_type=EmailEventType.ACCOUNT_CONFIRMATION,
        to_email=to_email,
        template_model={
            "user_name": user_name,
            "verification_url": build_email_verification_url(uid, to_email),
        },
    )


def try_send_signup_emails(
    *,
    full_name: str,
    account_email: Optional[str] = None,
    user_id: Optional[str] = None,
) -> None:
    """Fire-and-forget; never raises."""
    to_email = (account_email or "").strip()
    uid = (user_id or "").strip()
    if not to_email or not uid:
        logger.warning("Signup emails skipped: missing recipient or user id.")
        return

    try:
        if not send_signup_welcome_email(full_name=full_name, account_email=to_email):
            logger.warning("Signup welcome email was not sent.")
    except Exception as e:
        logger.warning("Signup welcome email failed: %s", e)

    try:
        if not send_verify_email_confirmation(
            full_name=full_name,
            account_email=to_email,
            user_id=uid,
        ):
            logger.warning("Verify email confirmation was not sent.")
    except Exception as e:
        logger.warning("Verify email confirmation failed: %s", e)
