import os

from app.email.enums import EmailEventType, SenderType

POSTMARK_TOKEN_ENV_KEYS = (
    "POSTMARK_SERVER_API_TOKEN",
    "POSTMARK-SERVER-API-TOKEN",
)

# Default From addresses (override per lane with EMAIL_FROM_HELLO / EMAIL_FROM_ALERTS / EMAIL_FROM_SUPPORT).
# HELLO: welcome, account confirmation, password reset, general system communication.
# ALERTS: pitch ready, new opportunity, reminder to submit, deadline approaching.
# SUPPORT: customer support, help requests, billing questions.
SENDER_EMAILS = {
    SenderType.HELLO: "hello@speakerpitcher.ai",
    SenderType.ALERTS: "alerts@speakerpitcher.ai",
    SenderType.SUPPORT: "support@speakerpitcher.ai",
}

# Default Postmark template id and alias per event (override with POSTMARK_TEMPLATE_ID_* / POSTMARK_TEMPLATE_ALIAS_*).
DEFAULT_POSTMARK_TEMPLATES: dict[EmailEventType, tuple[int, str]] = {
    EmailEventType.WELCOME_EMAIL: (44976911, "welcome_mail"),
    EmailEventType.ACCOUNT_CONFIRMATION: (44993827, "AccountSet_up_confirmation"),
    EmailEventType.PASSWORD_RESET: (44993926, "Password_reset"),
    EmailEventType.SYSTEM_NOTIFICATION: (44993828, "General_system_communication"),
    EmailEventType.ALERT_PITCH_READY: (44993949, "Pitch_ready"),
    EmailEventType.ALERT_NEW_OPPORTUNITY: (44993950, "New_opportunity"),
    EmailEventType.ALERT_SUBMISSION_REMINDER: (44993960, "Reminder_submition"),
    EmailEventType.ALERT_DEADLINE_APPROACHING: (44993834, "Deadline_approaching"),
    EmailEventType.SUPPORT_CUSTOMER_SUPPORT: (44993930, "Customer_support"),
    EmailEventType.SUPPORT_HELP_REQUEST: (44993931, "Help_request"),
    EmailEventType.SUPPORT_BILLING_QUESTION: (44993836, "Billing_questions"),
}

# Variable names expected by each Postmark template (used for default empty models).
TEMPLATE_VARIABLE_KEYS: dict[EmailEventType, tuple[str, ...]] = {
    EmailEventType.WELCOME_EMAIL: ("preheader", "user_name", "cta_url"),
    EmailEventType.ACCOUNT_CONFIRMATION: ("user_name", "verification_url"),
    EmailEventType.PASSWORD_RESET: ("user_name", "reset_password_url"),
    EmailEventType.SYSTEM_NOTIFICATION: (
        "hero_image_url",
        "update_title",
        "user_name",
        "intro_message",
        "feature_title",
        "feature_description",
        "body_message",
        "cta_url",
        "cta_text",
    ),
    EmailEventType.ALERT_PITCH_READY: (
        "user_name",
        "event_name",
        "event_date",
        "event_location",
        "deadline_date",
        "pitch_review_url",
    ),
    EmailEventType.ALERT_NEW_OPPORTUNITY: (
        "user_name",
        "event_name",
        "event_date",
        "event_location",
        "deadline_date",
        "fit_score",
        "opportunity_url",
    ),
    EmailEventType.ALERT_SUBMISSION_REMINDER: (
        "user_name",
        "event_name",
        "event_date",
        "event_location",
        "deadline_date",
        "submission_url",
    ),
    EmailEventType.ALERT_DEADLINE_APPROACHING: (
        "user_name",
        "event_name",
        "event_date",
        "event_location",
        "deadline_date",
        "days_remaining",
        "submission_url",
    ),
    EmailEventType.SUPPORT_CUSTOMER_SUPPORT: (
        "user_name",
        "support_response",
        "agent_name",
        "support_ticket_url",
        "ticket_id",
    ),
    EmailEventType.SUPPORT_HELP_REQUEST: (
        "user_name",
        "ticket_id",
        "ticket_subject",
        "submitted_date",
        "ticket_status",
        "support_ticket_url",
        "response_time_estimate",
    ),
    EmailEventType.SUPPORT_BILLING_QUESTION: (
        "billing_heading",
        "user_name",
        "billing_title",
        "invoice_id",
        "plan_name",
        "billing_amount",
        "billing_status",
        "billing_message",
        "billing_portal_url",
    ),
}


def empty_template_model(event_type: EmailEventType) -> dict[str, str]:
    return {k: "" for k in TEMPLATE_VARIABLE_KEYS[event_type]}


def get_postmark_template_for_event(event_type: EmailEventType) -> tuple[int | None, str | None]:
    """
    Optional env overrides:
      POSTMARK_TEMPLATE_ID_{EVENT_ENUM_NAME}
      POSTMARK_TEMPLATE_ALIAS_{EVENT_ENUM_NAME}
    """
    suffix = event_type.name
    raw_id = (os.getenv(f"POSTMARK_TEMPLATE_ID_{suffix}") or "").strip()
    raw_alias = (os.getenv(f"POSTMARK_TEMPLATE_ALIAS_{suffix}") or "").strip()
    template_id: int | None = int(raw_id) if raw_id.isdigit() else None
    template_alias = raw_alias or None
    return template_id, template_alias


def resolve_postmark_template(event_type: EmailEventType) -> tuple[int, str]:
    env_id, env_alias = get_postmark_template_for_event(event_type)
    default_id, default_alias = DEFAULT_POSTMARK_TEMPLATES[event_type]
    template_id = env_id if env_id is not None else default_id
    template_alias = env_alias if env_alias else default_alias
    return template_id, template_alias
