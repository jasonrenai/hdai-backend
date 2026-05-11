from enum import Enum


class EmailEventType(str, Enum):
    WELCOME_EMAIL = "welcome_email"
    PASSWORD_RESET = "password_reset"
    ACCOUNT_CONFIRMATION = "account_confirmation"
    SYSTEM_NOTIFICATION = "system_notification"
    ALERT_PITCH_READY = "alert_pitch_ready"
    ALERT_NEW_OPPORTUNITY = "alert_new_opportunity"
    ALERT_SUBMISSION_REMINDER = "alert_submission_reminder"
    ALERT_DEADLINE_APPROACHING = "alert_deadline_approaching"
    SUPPORT_CUSTOMER_SUPPORT = "support_customer_support"
    SUPPORT_HELP_REQUEST = "support_help_request"
    SUPPORT_BILLING_QUESTION = "support_billing_question"


class SenderType(str, Enum):
    HELLO = "hello"
    ALERTS = "alerts"
    SUPPORT = "support"
