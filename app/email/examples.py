from app.email.enums import EmailEventType
from app.email.service import EmailService


def send_welcome_email_example(email_service: EmailService, recipient_email: str, user_name: str) -> bool:
    return email_service.send_event_email(
        event_type=EmailEventType.WELCOME_EMAIL,
        to_email=recipient_email,
        user_name=user_name,
        cta_url="https://app.speakerpitcher.ai/dashboard",
        template_model_overrides={
            "body": "Your account is active. Complete your profile to get better speaking opportunity matches.",
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
        user_name=user_name,
        cta_url=reset_url,
        template_model_overrides={
            "preheader": "Reset your password securely.",
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
        user_name=user_name,
        cta_url=confirmation_url,
        template_model_overrides={
            "body": "Verify your email to complete account setup and unlock all features.",
        },
    )

