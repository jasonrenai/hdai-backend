"""Send speaker login credentials via Postmark (HTML + plain text, no template)."""
import html
import logging

from postmarker.core import PostmarkClient

from app.email.enums import SenderType
from app.email.helpers import (
    get_postmark_server_token,
    is_email_sending_enabled,
    resolve_sender_email,
)

logger = logging.getLogger(__name__)


def build_speaker_credentials_html(full_name: str, login_email: str, plain_password: str) -> str:
    safe_name = html.escape((full_name or "").strip() or "there")
    safe_email = html.escape((login_email or "").strip())
    safe_pw = html.escape(plain_password)
    return f"""<!DOCTYPE html>
<html>
<body>
<p>Hi {safe_name},</p>
<p>Your speaker account has been created. Use the credentials below to sign in:</p>
<p><strong>Username (email):</strong> {safe_email}<br/>
<strong>Temporary password:</strong> {safe_pw}</p>
<p>Please change your password after logging in.</p>
</body>
</html>"""


def build_speaker_credentials_text(full_name: str, login_email: str, plain_password: str) -> str:
    name = (full_name or "").strip() or "there"
    email_line = (login_email or "").strip()
    return (
        f"Hi {name},\n\n"
        f"Your speaker account has been created. Use the credentials below to sign in:\n\n"
        f"Username (email): {email_line}\n"
        f"Temporary password: {plain_password}\n\n"
        f"Please change your password after logging in.\n"
    )


def send_speaker_credentials_email(to_email: str, full_name: str, plain_password: str) -> bool:
    if not is_email_sending_enabled():
        logger.info(
            "Email sending disabled (EMAIL_SENDING_ENABLED=false); skipped speaker credentials email to %s",
            (to_email or "").strip(),
        )
        return False
    from_email = resolve_sender_email(SenderType.HELLO)
    token = get_postmark_server_token()
    if not from_email or not token or not (to_email or "").strip():
        logger.warning(
            "Skipping speaker credentials email: missing sender (EMAIL_FROM_HELLO or default), Postmark token, or recipient"
        )
        return False
    to_addr = to_email.strip()
    try:
        client = PostmarkClient(token)
        client.emails.send(
            From=from_email,
            To=to_addr,
            Subject="Your speaker account login",
            HtmlBody=build_speaker_credentials_html(full_name, to_addr, plain_password),
            TextBody=build_speaker_credentials_text(full_name, to_addr, plain_password),
        )
        return True
    except Exception as e:
        logger.warning("Failed to send speaker credentials email via Postmark: %s", e)
        return False
