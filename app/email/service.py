import logging
from typing import Any, Dict, Optional

from postmarker.core import PostmarkClient

from app.email.constants import resolve_postmark_template
from app.email.event_registry import EMAIL_EVENT_REGISTRY
from app.email.enums import EmailEventType, SenderType
from app.email.helpers import (
    get_postmark_server_token,
    normalize_template_model,
    resolve_sender_email,
)

logger = logging.getLogger(__name__)


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
        if not (to_email or "").strip():
            raise ValueError("Recipient email is required.")
        if not (from_email or "").strip():
            raise ValueError("Sender email is required.")
        if not template_id and not template_alias:
            raise ValueError("Either template_id or template_alias is required.")

        payload = {
            "From": from_email.strip(),
            "To": to_email.strip(),
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
        config = self.event_registry.get(event_type)
        if not config:
            raise ValueError(f"Unsupported email event: {event_type}")

        from_email = resolve_sender_email(sender or config.sender)
        model: Dict[str, Any] = dict(config.default_template_model)
        if template_model:
            model.update(template_model)

        template_id, template_alias = resolve_postmark_template(event_type)

        def _log_sent(*, used_template_id: Optional[int], used_alias: str) -> None:
            logger.info(
                "Sent %s email to %s from %s (template_id=%s, template_alias=%s)",
                event_type.value,
                to_email.strip(),
                from_email,
                used_template_id,
                used_alias,
            )

        try:
            self.send_template_email(
                to_email=to_email,
                from_email=from_email,
                template_id=template_id,
                template_alias=template_alias,
                template_model=model,
            )
            _log_sent(used_template_id=template_id, used_alias=template_alias)
            return True
        except Exception as exc:
            if "[1101]" in str(exc):
                try:
                    self.send_template_email(
                        to_email=to_email,
                        from_email=from_email,
                        template_id=None,
                        template_alias=template_alias,
                        template_model=model,
                    )
                    _log_sent(used_template_id=None, used_alias=template_alias)
                    return True
                except Exception as alias_exc:
                    logger.warning(
                        "Failed sending %s email to %s with alias fallback: %s",
                        event_type.value,
                        to_email,
                        alias_exc,
                    )
                    return False
            logger.warning("Failed sending %s email to %s: %s", event_type.value, to_email, exc)
            return False
