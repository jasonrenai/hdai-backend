from app.email.enums import EmailEventType
from app.email.service import EmailService


def send_welcome_email_example(email_service: EmailService, recipient_email: str, user_name: str) -> bool:
    return email_service.send_event_email(
        event_type=EmailEventType.WELCOME_EMAIL,
        to_email=recipient_email,
        template_model={
            "preheader": "Your account is ready to explore speaking opportunities.",
            "user_name": user_name,
            "cta_url": "https://app.speakerpitcher.ai/dashboard",
        },
    )


def send_password_reset_email_example(
    email_service: EmailService,
    recipient_email: str,
    user_name: str,
    reset_url: str,
) -> bool:
    return email_service.send_event_email(
        event_type=EmailEventType.PASSWORD_RESET,
        to_email=recipient_email,
        template_model={
            "user_name": user_name,
            "reset_password_url": reset_url,
        },
    )


def send_account_confirmation_email_example(
    email_service: EmailService,
    recipient_email: str,
    user_name: str,
    confirmation_url: str,
) -> bool:
    return email_service.send_event_email(
        event_type=EmailEventType.ACCOUNT_CONFIRMATION,
        to_email=recipient_email,
        template_model={
            "user_name": user_name,
            "verification_url": confirmation_url,
        },
    )
