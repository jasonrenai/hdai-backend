# SpeakerPitcher Email Module

Reusable Postmark email architecture. Each event type has its own template alias
and TemplateModel variables. HTML/text bodies live in `app/email/postmark_templates.py`.

## Folder Structure

- `app/email/constants.py` - template IDs/aliases, sender addresses, env keys.
- `app/email/enums.py` - `EmailEventType`, `SenderType`.
- `app/email/schemas.py` - event configuration dataclass.
- `app/email/event_registry.py` - event-to-template defaults and sender mapping.
- `app/email/helpers.py` - token resolution, sender resolution, template model normalization.
- `app/email/service.py` - centralized `EmailService`.
- `app/email/postmark_templates.py` - branded HTML/text bodies synced to Postmark.

## Required Environment Variables

- `POSTMARK_SERVER_API_TOKEN` (required for sending; **Server** API token, not the Account API token)
- `POSTMARK-SERVER-API-TOKEN` (legacy fallback)
- `EMAIL_FROM_HELLO` (optional override, defaults to `hello@speakerpitcher.ai`)
- `EMAIL_FROM_ALERTS` (optional override, defaults to `alerts@speakerpitcher.ai`)
- `EMAIL_FROM_SUPPORT` (optional override, defaults to `support@speakerpitcher.ai`)
- `FRONTEND_BASE_URL` (optional; email links. Defaults to the Nexus Static Web App)
- `PITCH_REVIEW_FRONTEND_BASE` / `EMAIL_VERIFICATION_FRONTEND_BASE` (optional; fall back to `FRONTEND_BASE_URL`)

Postmark has two token types:

- **Server API token** (`X-Postmark-Server-Token`): send mail, manage templates. Copy it from Postmark → Servers → (server) → API Tokens. This is what the app must use.
- **Account API token** (`X-Postmark-Account-Token`): create/list servers and sender signatures only. It cannot send mail or create templates.

If `POSTMARK_SERVER_API_TOKEN` is set to an account token, sends return 401.

Sends use Postmark **template aliases** (see `DEFAULT_POSTMARK_TEMPLATES`). Numeric template IDs from another Postmark account are ignored unless you set `POSTMARK_TEMPLATE_ID_{EVENT_ENUM_NAME}`.

Create or update aliases on a Postmark server:

```
POSTMARK_SERVER_API_TOKEN=... python scripts/sync_postmark_templates.py
POSTMARK_ACCOUNT_API_TOKEN=... python scripts/sync_postmark_templates.py
POSTMARK_ACCOUNT_API_TOKEN=... POSTMARK_SERVER_NAME=Nexus python scripts/sync_postmark_templates.py
python scripts/sync_postmark_templates.py --dry-run
```

The sync script can use an account token to look up the matching **server** token, then create templates with that. The running app still needs the server token.

## Event Types

| Event | Alias | Sender |
| --- | --- | --- |
| `welcome_email` | `welcome_mail` | hello |
| `signup_welcome_email` | `Welcome_mail_after_signup` | hello |
| `account_confirmation` | `Verify_email_confirmation` | hello |
| `password_reset` | `Password_reset` | hello |
| `system_notification` | `General_system_communication` | hello |
| `alert_pitch_ready` | `Pitch_ready` | alerts |
| `alert_new_opportunity` | `New_opportunity` | alerts |
| `alert_submission_reminder` | `Reminder_submition` | alerts |
| `alert_deadline_approaching` | `Deadline_approaching` | alerts |
| `support_customer_support` | `Customer_support` | support |
| `support_help_request` | `Help_request` | support |
| `support_billing_question` | `Billing_questions` | support |

Template variables for each event are listed in `TEMPLATE_VARIABLE_KEYS`.
