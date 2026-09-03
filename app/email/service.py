import logging
from typing import Any, Dict, Optional

from postmarker.core import PostmarkClient

from app.email.constants import resolve_postmark_template
from app.email.event_registry import EMAIL_EVENT_REGISTRY
from app.email.enums import EmailEventType, SenderType
from app.email.helpers import (
    get_postmark_server_token,
    is_email_sending_enabled,
    normalize_template_model,
    resolve_sender_email,
)

logger = logging.getLogger(__name__)

# Postmark inactive template error (send by alias fallback).
_POSTMARK_TEMPLATE_INACTIVE_MARKER = "[1101]"


class EmailService:
    def __init__(
        self,
        postmark_client: Optional[PostmarkClient] = None,
        event_registry: Optional[Dict[EmailEventType, Any]] = None,
    ):
        self.event_registry = event_registry or EMAIL_EVENT_REGISTRY
        self._postmark_client = postmark_client

    def _client(self) -> PostmarkClient:
        if self._postmark_client is not None:
            return self._postmark_client
        token = get_postmark_server_token()
        if not token:
            raise ValueError("Missing Postmark server API token.")
        self._postmark_client = PostmarkClient(token)
        return self._postmark_client

    def send_template_email(
        self,
        *,
        to_email: str,
        from_email: str,
        template_model: Dict[str, Any],
        template_id: Optional[int] = None,
        template_alias: Optional[str] = None,
    ) -> bool:
        if not is_email_sending_enabled():
            logger.info(
                "Email sending disabled (EMAIL_SENDING_ENABLED=false); skipped template email to %s",
                (to_email or "").strip(),
            )
            return False

        to = (to_email or "").strip()
        if not to:
            raise ValueError("Recipient email is required.")
        frm = (from_email or "").strip()
        if not frm:
            raise ValueError("Sender email is required.")
        if not template_id and not template_alias:
            raise ValueError("Either template_id or template_alias is required.")

        payload = {
            "From": frm,
            "To": to,
            "TemplateModel": normalize_template_model(template_model),
        }
        if template_id:
            payload["TemplateId"] = template_id
        else:
            payload["TemplateAlias"] = template_alias

        self._client().emails.send_with_template(**payload)
        return True

    def send_event_email(
        self,
        *,
        event_type: EmailEventType,
        to_email: str,
        template_model: Optional[Dict[str, Any]] = None,
        sender: Optional[SenderType] = None,
    ) -> bool:
        recipient = (to_email or "").strip()
        if not recipient:
            logger.warning("Skipped %s email: empty recipient", event_type.value)
            return False

        if not is_email_sending_enabled():
            logger.info(
                "Email sending disabled (EMAIL_SENDING_ENABLED=false); skipped %s to %s",
                event_type.value,
                recipient,
            )
            return False

        config = self.event_registry.get(event_type)
        if not config:
            raise ValueError(f"Unsupported email event: {event_type}")

        from_email = resolve_sender_email(sender or config.sender)
        overlay = template_model or {}
        model: Dict[str, Any] = {**config.default_template_model, **overlay}

        template_id, template_alias = resolve_postmark_template(event_type)
        used_template_id: Optional[int] = template_id

        def _send(*, tid: Optional[int], alias: str) -> None:
            self.send_template_email(
                to_email=recipient,
                from_email=from_email,
                template_id=tid,
                template_alias=alias,
                template_model=model,
            )

        try:
            _send(tid=template_id, alias=template_alias)
        except Exception as exc:
            if _POSTMARK_TEMPLATE_INACTIVE_MARKER not in str(exc):
                logger.warning(
                    "Failed sending %s email to %s: %s",
                    event_type.value,
                    recipient,
                    exc,
                )
                lowered = str(exc).lower()
                if "401" in lowered or "unauthorized" in lowered or "invalid" in lowered:
                    logger.warning(
                        "Postmark auth failed. POSTMARK_SERVER_API_TOKEN must be a "
                        "Server API token (Servers → API Tokens), not an Account API token."
                    )
                return False
            try:
                _send(tid=None, alias=template_alias)
                used_template_id = None
            except Exception as alias_exc:
                logger.warning(
                    "Failed sending %s email to %s with alias fallback: %s",
                    event_type.value,
                    recipient,
                    alias_exc,
                )
                return False

        logger.info(
            "Sent %s email to %s from %s (template_id=%s, template_alias=%s)",
            event_type.value,
            recipient,
            from_email,
            used_template_id,
            template_alias,
        )
        return True
