"""SpeakerPitcher Postmark template HTML/text bodies, keyed by alias.

Templates use Mustache variables that match ``TEMPLATE_VARIABLE_KEYS``.
Sync to a Postmark server with ``scripts/sync_postmark_templates.py``.
"""

from __future__ import annotations

from app.email.constants import DEFAULT_POSTMARK_TEMPLATES, frontend_base_url
from app.email.enums import EmailEventType

# Nexus brand tokens (see frontend --brand-*).
NAVY = "#142a4b"
ORANGE = "#fb4515"
ORANGE_DARK = "#e03d12"
ORANGE_LIGHT = "#fff0eb"
BLACK = "#1e1e1e"
MUTED = "#667085"
BG = "#f0f2f8"
CARD = "#ffffff"
BORDER = "#dfe9f2"
FONT = "Arial, Helvetica, sans-serif"

ALIAS_BY_EVENT: dict[EmailEventType, str] = {
    event: alias for event, (_template_id, alias) in DEFAULT_POSTMARK_TEMPLATES.items()
}

ALIASES: tuple[str, ...] = tuple(ALIAS_BY_EVENT[event] for event in EmailEventType)


def _site_url() -> str:
    return frontend_base_url()


def _hidden_preheader(text: str) -> str:
    return (
        f'<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;'
        f'font-size:1px;line-height:1px;color:{BG};">{text}&nbsp;&zwnj;&nbsp;</div>'
    )


def _cta(url_var: str, label: str) -> str:
    return (
        '<table role="presentation" cellspacing="0" cellpadding="0" style="margin:24px 0 8px;">'
        "<tr><td>"
        f'<a href="{{{{{url_var}}}}}" '
        f'style="display:inline-block;background:{ORANGE};color:{CARD};text-decoration:none;'
        "font-size:14px;font-weight:700;line-height:1.2;padding:12px 22px;border-radius:8px;"
        f'font-family:{FONT};">{label}</a>'
        "</td></tr></table>"
    )


def _cta_if(url_var: str, label: str) -> str:
    return f"{{{{#{url_var}}}}}{_cta(url_var, label)}{{{{/{url_var}}}}}"


def _layout(inner: str, *, preheader: str = "") -> str:
    site = _site_url()
    preheader_html = _hidden_preheader(preheader) if preheader else ""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="x-ua-compatible" content="ie=edge">
  <title>SpeakerPitcher</title>
</head>
<body style="margin:0;padding:0;background:{BG};font-family:{FONT};color:{BLACK};">
  {preheader_html}
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:{BG};">
    <tr>
      <td align="center" style="padding:24px 12px;">
        <table role="presentation" width="600" cellspacing="0" cellpadding="0" style="width:100%;max-width:600px;">
          <tr>
            <td style="background:{NAVY};border-radius:12px 12px 0 0;padding:20px 32px;">
              <p style="margin:0;font-size:18px;font-weight:700;letter-spacing:0.2px;">
                <a href="{site}" style="color:{CARD};text-decoration:none;">SpeakerPitcher</a>
                <span style="color:{ORANGE};">.</span>
              </p>
            </td>
          </tr>
          <tr>
            <td style="background:{CARD};padding:32px;font-size:15px;line-height:1.6;color:{BLACK};border-left:1px solid {BORDER};border-right:1px solid {BORDER};">
              {inner}
            </td>
          </tr>
          <tr>
            <td style="background:{NAVY};border-radius:0 0 12px 12px;padding:20px 32px;color:{CARD};font-size:12px;line-height:1.6;">
              <p style="margin:0 0 8px;color:{CARD};">SpeakerPitcher is brought to you by Human Driven AI.</p>
              <p style="margin:0;color:#9aa8bc;">2897 N. Druid Hills Road, Suite 328, Atlanta, GA 30329<br>
              <a href="{site}" style="color:{ORANGE};text-decoration:none;">Open SpeakerPitcher</a>
              &nbsp;·&nbsp;
              <a href="mailto:support@speakerpitcher.ai" style="color:{ORANGE};text-decoration:none;">support@speakerpitcher.ai</a></p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def _greeting() -> str:
    return f'<p style="margin:0 0 16px;">Hi {{{{user_name}}}},</p>'


def _heading(text: str) -> str:
    return (
        f'<p style="margin:0 0 16px;font-size:22px;line-height:1.3;font-weight:700;color:{NAVY};">'
        f"{text}</p>"
    )


def _paragraph(text: str, *, margin: str = "0 0 16px") -> str:
    return f'<p style="margin:{margin};">{text}</p>'


def _detail_card(rows_html: str) -> str:
    return (
        f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
        f'style="margin:8px 0 20px;border:1px solid {BORDER};border-radius:8px;background:{ORANGE_LIGHT};">'
        f'<tr><td style="padding:16px;">{rows_html}</td></tr></table>'
    )


def _opportunity_cards(*, extra_html: str = "", url_var: str = "opportunity_url") -> str:
    title_link = (
        f"{{{{#{url_var}}}}}"
        f'<a href="{{{{{url_var}}}}}" style="color:{NAVY};font-weight:700;text-decoration:none;font-size:16px;">{{{{title}}}}</a>'
        f"{{{{/{url_var}}}}}"
        f"{{{{^{url_var}}}}}"
        f'<span style="color:{NAVY};font-weight:700;font-size:16px;">{{{{title}}}}</span>'
        f"{{{{/{url_var}}}}}"
    )
    return (
        "{{#opportunities}}"
        f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
        f'style="margin:0 0 12px;border:1px solid {BORDER};border-radius:8px;">'
        '<tr><td style="padding:16px;">'
        f"{title_link}"
        f'<p style="margin:8px 0 0;color:{MUTED};font-size:13px;line-height:1.5;">'
        "{{date}} · {{location}}</p>"
        f'<p style="margin:4px 0 0;color:{ORANGE_DARK};font-size:13px;font-weight:700;">'
        "Deadline {{deadline}}</p>"
        f"{extra_html}"
        "</td></tr></table>"
        "{{/opportunities}}"
    )


def _event_summary_card() -> str:
    return _detail_card(
        f'<p style="margin:0;font-size:16px;font-weight:700;color:{NAVY};">{{{{event_name}}}}</p>'
        f'<p style="margin:8px 0 0;color:{MUTED};font-size:13px;">{{{{event_date}}}} · {{{{event_location}}}}</p>'
        f'<p style="margin:4px 0 0;color:{ORANGE_DARK};font-size:13px;font-weight:700;">Deadline {{{{deadline_date}}}}</p>'
    )


TEMPLATES: dict[str, dict[str, str]] = {
    "welcome_mail": {
        "Name": "Welcome",
        "Subject": "Welcome to SpeakerPitcher",
        "TextBody": (
            "Hi {{user_name}},\n\n"
            "Welcome to SpeakerPitcher. Complete your setup to start matching speaking opportunities.\n\n"
            "Complete your setup: {{cta_url}}\n"
        ),
        "HtmlBody": _layout(
            _greeting()
            + _heading("Welcome to SpeakerPitcher")
            + _paragraph(
                "Your account is ready. Complete your setup so we can match you with speaking opportunities that fit your expertise."
            )
            + _cta("cta_url", "Complete your setup"),
            preheader="{{preheader}}",
        ),
    },
    "Welcome_mail_after_signup": {
        "Name": "Welcome after signup",
        "Subject": "Welcome to SpeakerPitcher",
        "TextBody": (
            "Hi {{user_name}},\n\n"
            "Your SpeakerPitcher account is ready. Sign in and start building your speaker profile.\n\n"
            f"Sign in: {_site_url()}\n"
        ),
        "HtmlBody": _layout(
            _greeting()
            + _heading("Your account is ready")
            + _paragraph(
                "You can sign in and start building your speaker profile. We will use it to find aligned speaking opportunities."
            )
            + (
                '<table role="presentation" cellspacing="0" cellpadding="0" style="margin:24px 0 8px;">'
                "<tr><td>"
                f'<a href="{_site_url()}" '
                f'style="display:inline-block;background:{ORANGE};color:{CARD};text-decoration:none;'
                "font-size:14px;font-weight:700;line-height:1.2;padding:12px 22px;border-radius:8px;"
                f'font-family:{FONT};">Sign in</a>'
                "</td></tr></table>"
            ),
            preheader="Your SpeakerPitcher account is ready.",
        ),
    },
    "Verify_email_confirmation": {
        "Name": "Verify email confirmation",
        "Subject": "Confirm your SpeakerPitcher email",
        "TextBody": (
            "Hi {{user_name}},\n\n"
            "Please confirm your email address to finish setting up your account.\n\n"
            "Verify email: {{verification_url}}\n"
        ),
        "HtmlBody": _layout(
            _greeting()
            + _heading("Confirm your email")
            + _paragraph("Please confirm your email address to finish setting up your SpeakerPitcher account.")
            + _cta("verification_url", "Verify email"),
            preheader="Confirm your email to finish setting up SpeakerPitcher.",
        ),
    },
    "Password_reset": {
        "Name": "Password reset",
        "Subject": "Your SpeakerPitcher reset code",
        "TextBody": (
            "Hi {{user_name}},\n\n"
            "Use this code to reset your password: {{otp}}\n\n"
            "If you did not request this, you can ignore this email.\n"
        ),
        "HtmlBody": _layout(
            _greeting()
            + _heading("Reset your password")
            + _paragraph("Use this code to reset your SpeakerPitcher password:")
            + (
                f'<p style="margin:16px 0;padding:16px 20px;background:{ORANGE_LIGHT};border-radius:8px;'
                f'text-align:center;font-size:32px;letter-spacing:6px;font-weight:700;color:{NAVY};">{{{{otp}}}}</p>'
            )
            + _paragraph("If you did not request this, you can ignore this email."),
            preheader="Your SpeakerPitcher password reset code.",
        ),
    },
    "General_system_communication": {
        "Name": "General system communication",
        "Subject": "{{update_title}}",
        "TextBody": (
            "Hi {{user_name}},\n\n"
            "{{intro_message}}\n\n"
            "{{feature_title}}\n"
            "{{feature_description}}\n\n"
            "{{body_message}}\n\n"
            "{{cta_text}}: {{cta_url}}\n"
        ),
        "HtmlBody": _layout(
            "{{#hero_image_url}}"
            f'<p style="margin:0 0 20px;"><img src="{{{{hero_image_url}}}}" alt="" width="536" '
            f'style="display:block;max-width:100%;height:auto;border:0;border-radius:8px;"></p>'
            "{{/hero_image_url}}"
            + _greeting()
            + _heading("{{update_title}}")
            + _paragraph("{{intro_message}}")
            + "{{#feature_title}}"
            + _detail_card(
                f'<p style="margin:0;font-weight:700;color:{NAVY};">{{{{feature_title}}}}</p>'
                f'<p style="margin:8px 0 0;color:{BLACK};">{{{{feature_description}}}}</p>'
            )
            + "{{/feature_title}}"
            + _paragraph("{{body_message}}")
            + "{{#cta_url}}"
            + (
                '<table role="presentation" cellspacing="0" cellpadding="0" style="margin:24px 0 8px;">'
                "<tr><td>"
                f'<a href="{{{{cta_url}}}}" '
                f'style="display:inline-block;background:{ORANGE};color:{CARD};text-decoration:none;'
                "font-size:14px;font-weight:700;line-height:1.2;padding:12px 22px;border-radius:8px;"
                f'font-family:{FONT};">{{{{cta_text}}}}</a>'
                "</td></tr></table>"
            )
            + "{{/cta_url}}",
            preheader="{{update_title}}",
        ),
    },
    "Pitch_ready": {
        "Name": "Pitch ready",
        "Subject": "Your pitch for {{event_name}} is ready",
        "TextBody": (
            "Hi {{user_name}},\n\n"
            "Your pitch for {{event_name}} is ready to review.\n"
            "{{event_date}} · {{event_location}}\n"
            "Deadline: {{deadline_date}}\n\n"
            "Review your pitch: {{pitch_review_url}}\n"
        ),
        "HtmlBody": _layout(
            _greeting()
            + _heading("Your pitch is ready")
            + _paragraph("Review and approve your pitch for this speaking opportunity:")
            + _event_summary_card()
            + _cta("pitch_review_url", "Review your pitch"),
            preheader="Your pitch for {{event_name}} is ready to review.",
        ),
    },
    "New_opportunity": {
        "Name": "New opportunity",
        "Subject": "New speaking opportunities for you",
        "TextBody": (
            "Hi {{user_name}},\n\n"
            "Here are new speaking opportunities matched to your profile:\n\n"
            "{{#opportunities}}"
            "- {{title}}\n"
            "  {{date}} · {{location}} · Deadline {{deadline}}\n"
            "  {{opportunity_url}}\n\n"
            "{{/opportunities}}"
        ),
        "HtmlBody": _layout(
            _greeting()
            + _heading("New speaking opportunities")
            + _paragraph("Here are new speaking opportunities matched to your profile:")
            + _opportunity_cards(),
            preheader="New speaking opportunities matched to your profile.",
        ),
    },
    "Reminder_submition": {
        "Name": "Reminder to submit",
        "Subject": "Reminder: submit for {{event_name}}",
        "TextBody": (
            "Hi {{user_name}},\n\n"
            "This is a reminder to submit for {{event_name}}.\n"
            "{{event_date}} · {{event_location}}\n"
            "Deadline: {{deadline_date}}\n\n"
            "Open submission: {{submission_url}}\n"
        ),
        "HtmlBody": _layout(
            _greeting()
            + _heading("Reminder to submit")
            + _paragraph("This is a reminder to submit for the speaking opportunity below.")
            + _event_summary_card()
            + _cta_if("submission_url", "Open submission"),
            preheader="Reminder to submit for {{event_name}}.",
        ),
    },
    "Deadline_approaching": {
        "Name": "Deadline approaching",
        "Subject": "Deadline approaching: {{event_name}}",
        "TextBody": (
            "Hi {{user_name}},\n\n"
            "{{intro}}\n\n"
            "{{#opportunities}}"
            "- {{title}}\n"
            "  {{date}} · {{location}} · Deadline {{deadline}} ({{days_remaining}} days remaining)\n"
            "  {{opportunity_url}}\n\n"
            "{{/opportunities}}"
            "{{^opportunities}}"
            "{{event_name}}\n"
            "{{event_date}} · {{event_location}}\n"
            "Deadline: {{deadline_date}} ({{days_remaining}} days remaining)\n"
            "Submit: {{submission_url}}\n"
            "{{/opportunities}}"
        ),
        "HtmlBody": _layout(
            _greeting()
            + _heading("Deadlines approaching")
            + _paragraph("{{intro}}")
            + "{{#opportunities}}"
            f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
            f'style="margin:0 0 12px;border:1px solid {BORDER};border-radius:8px;">'
            '<tr><td style="padding:16px;">'
            f'<a href="{{{{opportunity_url}}}}" style="color:{NAVY};font-weight:700;text-decoration:none;font-size:16px;">{{{{title}}}}</a>'
            f'<p style="margin:8px 0 0;color:{MUTED};font-size:13px;line-height:1.5;">'
            "{{date}} · {{location}}</p>"
            f'<p style="margin:4px 0 0;color:{ORANGE_DARK};font-size:13px;font-weight:700;">'
            "Deadline {{deadline}} · {{days_remaining}} days remaining</p>"
            "{{#status_label}}"
            f'<p style="margin:8px 0 0;font-size:12px;font-weight:700;color:{NAVY};">{{{{status_label}}}}</p>'
            "{{/status_label}}"
            "{{#submission_url}}"
            f'<p style="margin:12px 0 0;"><a href="{{{{submission_url}}}}" style="color:{ORANGE};font-weight:700;text-decoration:none;">Submit now</a></p>'
            "{{/submission_url}}"
            "</td></tr></table>"
            "{{/opportunities}}"
            + "{{^opportunities}}"
            + _event_summary_card()
            + (
                f'<p style="margin:0 0 16px;color:{ORANGE_DARK};font-size:13px;font-weight:700;">'
                "{{days_remaining}} days remaining</p>"
            )
            + _cta_if("submission_url", "Submit now")
            + "{{/opportunities}}",
            preheader="{{intro}}",
        ),
    },
    "Customer_support": {
        "Name": "Customer support",
        "Subject": "Support update on ticket {{ticket_id}}",
        "TextBody": (
            "Hi {{user_name}},\n\n"
            "{{support_response}}\n\n"
            "Ticket {{ticket_id}} · {{agent_name}}\n\n"
            "View ticket: {{support_ticket_url}}\n"
        ),
        "HtmlBody": _layout(
            _greeting()
            + _heading("Support update")
            + _paragraph("{{support_response}}")
            + _detail_card(
                f'<p style="margin:0;color:{MUTED};font-size:13px;">Ticket {{{{ticket_id}}}}</p>'
                f'<p style="margin:4px 0 0;color:{NAVY};font-weight:700;">{{{{agent_name}}}}</p>'
            )
            + _cta_if("support_ticket_url", "View ticket"),
            preheader="Support update on ticket {{ticket_id}}.",
        ),
    },
    "Help_request": {
        "Name": "Help request",
        "Subject": "We received your request {{ticket_id}}",
        "TextBody": (
            "Hi {{user_name}},\n\n"
            "We received your request {{ticket_subject}} ({{ticket_id}}).\n"
            "Status: {{ticket_status}} · Submitted {{submitted_date}}\n"
            "Typical response time: {{response_time_estimate}}\n\n"
            "View request: {{support_ticket_url}}\n"
        ),
        "HtmlBody": _layout(
            _greeting()
            + _heading("We received your request")
            + _paragraph("Thanks for reaching out. We received your request <strong>{{ticket_subject}}</strong>.")
            + _detail_card(
                f'<p style="margin:0;color:{NAVY};font-weight:700;">Ticket {{{{ticket_id}}}}</p>'
                f'<p style="margin:8px 0 0;color:{MUTED};font-size:13px;">Status: {{{{ticket_status}}}} · Submitted {{{{submitted_date}}}}</p>'
                f'<p style="margin:8px 0 0;color:{MUTED};font-size:13px;">Typical response time: {{{{response_time_estimate}}}}</p>'
            )
            + _cta_if("support_ticket_url", "View request"),
            preheader="We received your request {{ticket_subject}}.",
        ),
    },
    "Billing_questions": {
        "Name": "Billing questions",
        "Subject": "{{billing_heading}}",
        "TextBody": (
            "Hi {{user_name}},\n\n"
            "{{billing_message}}\n\n"
            "{{billing_title}}\n"
            "Invoice {{invoice_id}} · {{plan_name}} · {{billing_amount}} · {{billing_status}}\n\n"
            "Download invoice: {{invoice_pdf_url.invoice_pdf_url}}\n"
        ),
        "HtmlBody": _layout(
            _greeting()
            + _heading("{{billing_heading}}")
            + _paragraph("{{billing_message}}")
            + _detail_card(
                f'<p style="margin:0;font-weight:700;color:{NAVY};">{{{{billing_title}}}}</p>'
                f'<p style="margin:8px 0 0;color:{MUTED};font-size:13px;">Invoice {{{{invoice_id}}}}</p>'
                f'<p style="margin:4px 0 0;color:{MUTED};font-size:13px;">Plan: {{{{plan_name}}}}</p>'
                f'<p style="margin:4px 0 0;color:{MUTED};font-size:13px;">Amount: {{{{billing_amount}}}}</p>'
                f'<p style="margin:4px 0 0;color:{MUTED};font-size:13px;">Status: {{{{billing_status}}}}</p>'
            )
            + "{{#invoice_pdf_url.invoice_pdf_url}}"
            + (
                '<table role="presentation" cellspacing="0" cellpadding="0" style="margin:24px 0 8px;">'
                "<tr><td>"
                f'<a href="{{{{invoice_pdf_url.invoice_pdf_url}}}}" '
                f'style="display:inline-block;background:{ORANGE};color:{CARD};text-decoration:none;'
                "font-size:14px;font-weight:700;line-height:1.2;padding:12px 22px;border-radius:8px;"
                f'font-family:{FONT};">Download invoice</a>'
                "</td></tr></table>"
            )
            + "{{/invoice_pdf_url.invoice_pdf_url}}",
            preheader="{{billing_heading}}",
        ),
    },
}


def template_bodies(alias: str) -> dict[str, str]:
    spec = TEMPLATES[alias]
    return {
        "Name": spec["Name"],
        "Alias": alias,
        "Subject": spec["Subject"],
        "HtmlBody": spec["HtmlBody"],
        "TextBody": spec["TextBody"],
        "TemplateType": "Standard",
    }
