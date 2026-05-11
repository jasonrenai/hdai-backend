from app.email.enums import SenderType
import os


POSTMARK_TEMPLATE_ID_SPEAKERPITCHER = int(os.getenv("POSTMARK_TEMPLATE_ID_SPEAKERPITCHER", "44976911"))
POSTMARK_TEMPLATE_ALIAS_SPEAKERPITCHER = os.getenv(
    "POSTMARK_TEMPLATE_ALIAS_SPEAKERPITCHER",
    "greetings_template",
)

POSTMARK_TOKEN_ENV_KEYS = (
    "POSTMARK_SERVER_API_TOKEN",
    "POSTMARK-SERVER-API-TOKEN",
)

SENDER_EMAILS = {
    SenderType.HELLO: "hello@speakerpitcher.ai",
    SenderType.ALERTS: "alerts@speakerpitcher.ai",
    SenderType.SUPPORT: "support@speakerpitcher.ai",
}

