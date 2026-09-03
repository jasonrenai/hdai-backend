# SpeakerPitcher Email Module

Reusable Postmark email architecture using one shared template and dynamic template model variables.

## Folder Structure

- `app/email/constants.py` - template IDs/aliases, sender addresses, env keys.
- `app/email/enums.py` - `EmailEventType`, `SenderType`.
- `app/email/schemas.py` - event configuration dataclass.
- `app/email/event_registry.py` - event-to-template defaults and sender mapping.
- `app/email/helpers.py` - token resolution, sender resolution, template model normalization.
- `app/email/service.py` - centralized `EmailService`.
- `app/email/examples.py` - reusable usage examples.

## Required Environment Variables

- `POSTMARK_SERVER_API_TOKEN` (preferred)
- `POSTMARK-SERVER-API-TOKEN` (legacy fallback)
- `EMAIL_FROM_HELLO` (optional override, defaults to `hello@speakerpitcher.ai`)
- `EMAIL_FROM_ALERTS` (optional override, defaults to `alerts@speakerpitcher.ai`)
- `EMAIL_FROM_SUPPORT` (optional override, defaults to `support@speakerpitcher.ai`)
- `FRONTEND_BASE_URL` (optional; email links. Defaults to the Nexus Static Web App)
- `PITCH_REVIEW_FRONTEND_BASE` / `EMAIL_VERIFICATION_FRONTEND_BASE` (optional; fall back to `FRONTEND_BASE_URL`)

Sends use Postmark **template aliases** (see `DEFAULT_POSTMARK_TEMPLATES`). Numeric template IDs from another Postmark account are ignored unless you set `POSTMARK_TEMPLATE_ID_{EVENT_ENUM_NAME}`.

Create or update aliases on a new Postmark server:

```
POSTMARK_SERVER_API_TOKEN=... python scripts/sync_postmark_templates.py
```

## Shared Postmark Template Variables

The service always sends these keys to Postmark:

- `subject`
- `preheader`
- `badge`
- `title`
- `user_name`
- `intro`
- `body`
- `cta_url`
- `cta_text`
- `secondary_note`

## Event Types

- `welcome_email`
- `password_reset`
- `account_confirmation`
- `system_notification`
- `alert_pitch_ready`
- `alert_new_opportunity`
- `alert_submission_reminder`
- `alert_deadline_approaching`
- `support_customer_support`
- `support_help_request`
- `support_billing_question`

Each event maps to:

- sender (`hello@speakerpitcher.ai`, `alerts@speakerpitcher.ai`, `support@speakerpitcher.ai`)
- Postmark template alias (`welcome_mail`, `New_opportunity`, …)
- default content model values

