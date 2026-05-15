"""Billing / invoice receipt email — Postmark Billing_questions template."""

from __future__ import annotations

import logging
from typing import Any, Mapping, Optional

from app.email.enums import EmailEventType

logger = logging.getLogger(__name__)

# Grep-friendly prefix for billing invoice email logs.
BILLING_EMAIL_LOG_PREFIX = "[billing-email]"


def _billing_log_summary(template_model: Mapping[str, Any]) -> str:
    nested = template_model.get("invoice_pdf_url")
    pdf_url = ""
    if isinstance(nested, dict):
        pdf_url = str(nested.get("invoice_pdf_url") or "")
    has_pdf = bool(pdf_url.strip())
    return (
        f"invoice_id={template_model.get('invoice_id')!r} "
        f"plan_name={template_model.get('plan_name')!r} "
        f"amount={template_model.get('billing_amount')!r} "
        f"status={template_model.get('billing_status')!r} "
        f"has_pdf_url={has_pdf}"
    )


def _format_billing_amount(amount_paid_cents: Any, currency: Optional[str]) -> str:
    if amount_paid_cents is None:
        return ""
    try:
        cents = int(amount_paid_cents)
    except (TypeError, ValueError):
        return ""
    cur = (currency or "usd").strip().upper()
    amount = cents / 100.0
    if cur == "USD":
        return f"${amount:,.2f}"
    return f"{amount:,.2f} {cur}"


def build_billing_template_model(
    *,
    user_name: str,
    invoice_id: str,
    plan_name: str,
    billing_amount: str,
    billing_status: str,
    invoice_pdf_url: Optional[str] = None,
    billing_heading: str = "Your payment receipt",
    billing_title: str = "Subscription invoice",
    billing_message: str = (
        "Thank you for your payment. Use the link below to view or download your invoice."
    ),
) -> dict[str, Any]:
    """
    Build TemplateModel for Postmark Billing_questions.

    ``invoice_pdf_url`` must be nested to match the template:
    ``{{invoice_pdf_url.invoice_pdf_url}}``
    """
    pdf = (invoice_pdf_url or "").strip()
    return {
        "billing_heading": billing_heading,
        "user_name": (user_name or "").strip(),
        "billing_message": billing_message,
        "billing_title": billing_title,
        "invoice_id": invoice_id,
        "plan_name": plan_name,
        "billing_amount": billing_amount,
        "billing_status": billing_status,
        "invoice_pdf_url": {"invoice_pdf_url": pdf},
    }


def build_billing_template_model_from_stripe_invoice(
    *,
    user_name: str,
    invoice: Mapping[str, Any],
    plan_name: str = "",
    billing_heading: str = "Your payment receipt",
    billing_title: str = "Subscription invoice",
    billing_message: Optional[str] = None,
) -> dict[str, Any]:
    """Map a Stripe invoice dict (or ``_invoice_to_row`` output) into the billing template model."""
    inv_id = str(invoice.get("id") or "")
    pdf = invoice.get("invoice_pdf") or invoice.get("invoice_pdf_url")
    return build_billing_template_model(
        user_name=user_name,
        invoice_id=inv_id,
        plan_name=plan_name,
        billing_amount=_format_billing_amount(invoice.get("amount_paid"), invoice.get("currency")),
        billing_status=str(invoice.get("status") or ""),
        invoice_pdf_url=str(pdf) if pdf else None,
        billing_heading=billing_heading,
        billing_title=billing_title,
        billing_message=billing_message
        or "Thank you for your payment. Use the link below to view or download your invoice.",
    )


def send_billing_invoice_email(
    *,
    to_email: str,
    template_model: dict[str, Any],
) -> bool:
    from app.dependencies import get_email_service
    from app.email.helpers import get_postmark_server_token

    recipient = (to_email or "").strip()
    summary = _billing_log_summary(template_model)
    invoice_id = template_model.get("invoice_id") or "unknown"

    if not recipient:
        logger.warning(
            "%s SKIP send — empty recipient | invoice_id=%s | %s",
            BILLING_EMAIL_LOG_PREFIX,
            invoice_id,
            summary,
        )
        return False

    if not get_postmark_server_token():
        logger.error(
            "%s FAIL send — Postmark token not configured | to=%s | %s",
            BILLING_EMAIL_LOG_PREFIX,
            recipient,
            summary,
        )
        return False

    logger.info(
        "%s START send | to=%s | %s",
        BILLING_EMAIL_LOG_PREFIX,
        recipient,
        summary,
    )

    try:
        ok = get_email_service().send_event_email(
            event_type=EmailEventType.SUPPORT_BILLING_QUESTION,
            to_email=recipient,
            template_model=template_model,
        )
    except Exception as e:
        logger.exception(
            "%s FAIL send — exception | to=%s | invoice_id=%s | error=%s",
            BILLING_EMAIL_LOG_PREFIX,
            recipient,
            invoice_id,
            e,
        )
        return False

    if ok:
        logger.info(
            "%s SUCCESS send | to=%s | invoice_id=%s | %s",
            BILLING_EMAIL_LOG_PREFIX,
            recipient,
            invoice_id,
            summary,
        )
    else:
        logger.warning(
            "%s FAIL send — Postmark returned false (check support_billing_question / template) | "
            "to=%s | invoice_id=%s | %s",
            BILLING_EMAIL_LOG_PREFIX,
            recipient,
            invoice_id,
            summary,
        )
    return ok
