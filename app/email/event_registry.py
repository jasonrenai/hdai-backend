from app.email.constants import empty_template_model
from app.email.enums import EmailEventType, SenderType
from app.email.schemas import EmailEventConfig


EMAIL_EVENT_REGISTRY = {
    EmailEventType.WELCOME_EMAIL: EmailEventConfig(
        event_type=EmailEventType.WELCOME_EMAIL,
        sender=SenderType.HELLO,
        default_template_model=empty_template_model(EmailEventType.WELCOME_EMAIL),
    ),
    EmailEventType.PASSWORD_RESET: EmailEventConfig(
        event_type=EmailEventType.PASSWORD_RESET,
        sender=SenderType.HELLO,
        default_template_model=empty_template_model(EmailEventType.PASSWORD_RESET),
    ),
    EmailEventType.ACCOUNT_CONFIRMATION: EmailEventConfig(
        event_type=EmailEventType.ACCOUNT_CONFIRMATION,
        sender=SenderType.HELLO,
        default_template_model=empty_template_model(EmailEventType.ACCOUNT_CONFIRMATION),
    ),
    EmailEventType.SYSTEM_NOTIFICATION: EmailEventConfig(
        event_type=EmailEventType.SYSTEM_NOTIFICATION,
        sender=SenderType.HELLO,
        default_template_model=empty_template_model(EmailEventType.SYSTEM_NOTIFICATION),
    ),
    EmailEventType.ALERT_PITCH_READY: EmailEventConfig(
        event_type=EmailEventType.ALERT_PITCH_READY,
        sender=SenderType.ALERTS,
        default_template_model=empty_template_model(EmailEventType.ALERT_PITCH_READY),
    ),
    EmailEventType.ALERT_NEW_OPPORTUNITY: EmailEventConfig(
        event_type=EmailEventType.ALERT_NEW_OPPORTUNITY,
        sender=SenderType.ALERTS,
        default_template_model=empty_template_model(EmailEventType.ALERT_NEW_OPPORTUNITY),
    ),
    EmailEventType.ALERT_SUBMISSION_REMINDER: EmailEventConfig(
        event_type=EmailEventType.ALERT_SUBMISSION_REMINDER,
        sender=SenderType.ALERTS,
        default_template_model=empty_template_model(EmailEventType.ALERT_SUBMISSION_REMINDER),
    ),
    EmailEventType.ALERT_DEADLINE_APPROACHING: EmailEventConfig(
        event_type=EmailEventType.ALERT_DEADLINE_APPROACHING,
        sender=SenderType.ALERTS,
        default_template_model=empty_template_model(EmailEventType.ALERT_DEADLINE_APPROACHING),
    ),
    EmailEventType.SUPPORT_CUSTOMER_SUPPORT: EmailEventConfig(
        event_type=EmailEventType.SUPPORT_CUSTOMER_SUPPORT,
        sender=SenderType.SUPPORT,
        default_template_model=empty_template_model(EmailEventType.SUPPORT_CUSTOMER_SUPPORT),
    ),
    EmailEventType.SUPPORT_HELP_REQUEST: EmailEventConfig(
        event_type=EmailEventType.SUPPORT_HELP_REQUEST,
        sender=SenderType.SUPPORT,
        default_template_model=empty_template_model(EmailEventType.SUPPORT_HELP_REQUEST),
    ),
    EmailEventType.SUPPORT_BILLING_QUESTION: EmailEventConfig(
        event_type=EmailEventType.SUPPORT_BILLING_QUESTION,
        sender=SenderType.SUPPORT,
        default_template_model=empty_template_model(EmailEventType.SUPPORT_BILLING_QUESTION),
    ),
}
