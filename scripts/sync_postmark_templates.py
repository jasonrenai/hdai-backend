"""Create or update SpeakerPitcher Postmark templates by alias.

Requires POSTMARK_SERVER_API_TOKEN (or POSTMARK-SERVER-API-TOKEN).
Idempotent: existing aliases are updated in place.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

POSTMARK_API = "https://api.postmarkapp.com"
ALIASES = (
    "welcome_mail",
    "Welcome_mail_after_signup",
    "Verify_email_confirmation",
    "Password_reset",
    "General_system_communication",
    "Pitch_ready",
    "New_opportunity",
    "Reminder_submition",
    "Deadline_approaching",
    "Customer_support",
    "Help_request",
    "Billing_questions",
)


def _token() -> str:
    for key in ("POSTMARK_SERVER_API_TOKEN", "POSTMARK-SERVER-API-TOKEN"):
        value = (os.getenv(key) or "").strip()
        if value:
            return value
    raise SystemExit("Set POSTMARK_SERVER_API_TOKEN")


def _layout(inner: str) -> str:
    return f"""<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:#f4f4f5;font-family:Arial,Helvetica,sans-serif;color:#111827;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f4f4f5;padding:24px 0;">
    <tr>
      <td align="center">
        <table role="presentation" width="600" cellspacing="0" cellpadding="0" style="background:#ffffff;border-radius:8px;padding:32px;">
          <tr>
            <td style="font-size:14px;line-height:1.6;">
              <p style="margin:0 0 16px;font-size:18px;font-weight:bold;">SpeakerPitcher</p>
              {inner}
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def _cta(url_var: str, label: str) -> str:
    return (
        f'<p style="margin:24px 0 0;"><a href="{{{{{url_var}}}}}" '
        f'style="display:inline-block;background:#111827;color:#ffffff;text-decoration:none;'
        f'padding:12px 20px;border-radius:6px;">{label}</a></p>'
    )


TEMPLATES = {
    "welcome_mail": {
        "Name": "Welcome",
        "Subject": "Welcome to SpeakerPitcher",
        "TextBody": "Hi {{user_name}}, welcome to SpeakerPitcher. Complete your setup: {{cta_url}}",
        "HtmlBody": _layout(
            "<p>Hi {{user_name}},</p>"
            "<p>{{preheader}}</p>"
            "<p>Welcome to SpeakerPitcher. Complete your setup to start matching speaking opportunities.</p>"
            + _cta("cta_url", "Complete your setup")
        ),
    },
    "Welcome_mail_after_signup": {
        "Name": "Welcome after signup",
        "Subject": "Welcome to SpeakerPitcher",
        "TextBody": "Hi {{user_name}}, your SpeakerPitcher account is ready.",
        "HtmlBody": _layout(
            "<p>Hi {{user_name}},</p>"
            "<p>Your SpeakerPitcher account is ready. You can sign in and start building your speaker profile.</p>"
        ),
    },
    "Verify_email_confirmation": {
        "Name": "Verify email confirmation",
        "Subject": "Confirm your SpeakerPitcher email",
        "TextBody": "Hi {{user_name}}, confirm your email: {{verification_url}}",
        "HtmlBody": _layout(
            "<p>Hi {{user_name}},</p>"
            "<p>Please confirm your email address to finish setting up your account.</p>"
            + _cta("verification_url", "Verify email")
        ),
    },
    "Password_reset": {
        "Name": "Password reset",
        "Subject": "Your SpeakerPitcher reset code",
        "TextBody": "Hi {{user_name}}, your password reset code is {{otp}}.",
        "HtmlBody": _layout(
            "<p>Hi {{user_name}},</p>"
            "<p>Use this code to reset your password:</p>"
            '<p style="font-size:28px;letter-spacing:4px;font-weight:bold;">{{otp}}</p>'
            "<p>If you did not request this, you can ignore this email.</p>"
        ),
    },
    "General_system_communication": {
        "Name": "General system communication",
        "Subject": "{{update_title}}",
        "TextBody": "Hi {{user_name}}, {{intro_message}} {{body_message}} {{cta_url}}",
        "HtmlBody": _layout(
            '<p><img src="{{hero_image_url}}" alt="" style="max-width:100%;"></p>'
            "<p>Hi {{user_name}},</p>"
            "<p>{{intro_message}}</p>"
            "<p><strong>{{feature_title}}</strong></p>"
            "<p>{{feature_description}}</p>"
            "<p>{{body_message}}</p>"
            '<p><a href="{{cta_url}}">{{cta_text}}</a></p>'
        ),
    },
    "Pitch_ready": {
        "Name": "Pitch ready",
        "Subject": "Your pitch for {{event_name}} is ready",
        "TextBody": "Hi {{user_name}}, your pitch for {{event_name}} ({{event_date}}, {{event_location}}) is ready. Deadline {{deadline_date}}. Review: {{pitch_review_url}}",
        "HtmlBody": _layout(
            "<p>Hi {{user_name}},</p>"
            "<p>Your pitch for <strong>{{event_name}}</strong> is ready to review.</p>"
            "<p>{{event_date}} · {{event_location}}<br>Deadline: {{deadline_date}}</p>"
            + _cta("pitch_review_url", "Review your pitch")
        ),
    },
    "New_opportunity": {
        "Name": "New opportunity",
        "Subject": "New speaking opportunities for you",
        "TextBody": "Hi {{user_name}}, new speaking opportunities are ready in SpeakerPitcher.",
        "HtmlBody": _layout(
            "<p>Hi {{user_name}},</p>"
            "<p>Here are new speaking opportunities matched to your profile:</p>"
            "<ul>"
            "{{#opportunities}}"
            "<li><a href=\"{{opportunity_url}}\">{{title}}</a>"
            "<br>{{date}} · {{location}} · Deadline {{deadline}}</li>"
            "{{/opportunities}}"
            "</ul>"
        ),
    },
    "Reminder_submition": {
        "Name": "Reminder to submit",
        "Subject": "Reminder: submit for {{event_name}}",
        "TextBody": "Hi {{user_name}}, reminder to submit for {{event_name}} by {{deadline_date}}. {{submission_url}}",
        "HtmlBody": _layout(
            "<p>Hi {{user_name}},</p>"
            "<p>This is a reminder to submit for <strong>{{event_name}}</strong>.</p>"
            "<p>{{event_date}} · {{event_location}}<br>Deadline: {{deadline_date}}</p>"
            + _cta("submission_url", "Open submission")
        ),
    },
    "Deadline_approaching": {
        "Name": "Deadline approaching",
        "Subject": "Deadline approaching: {{event_name}}",
        "TextBody": "Hi {{user_name}}, {{intro}} Deadline {{deadline_date}} ({{days_remaining}} days). {{submission_url}}",
        "HtmlBody": _layout(
            "<p>Hi {{user_name}},</p>"
            "<p>{{intro}}</p>"
            "<ul>"
            "{{#opportunities}}"
            "<li><a href=\"{{opportunity_url}}\">{{title}}</a>"
            "<br>{{date}} · {{location}} · Deadline {{deadline}}</li>"
            "{{/opportunities}}"
            "</ul>"
            "<p><strong>{{event_name}}</strong><br>{{event_date}} · {{event_location}}"
            "<br>Deadline: {{deadline_date}} ({{days_remaining}} days remaining)</p>"
            + _cta("submission_url", "Submit now")
        ),
    },
    "Customer_support": {
        "Name": "Customer support",
        "Subject": "Support update on ticket {{ticket_id}}",
        "TextBody": "Hi {{user_name}}, {{support_response}} Ticket {{ticket_id}}. {{support_ticket_url}}",
        "HtmlBody": _layout(
            "<p>Hi {{user_name}},</p>"
            "<p>{{support_response}}</p>"
            "<p>Ticket {{ticket_id}} · {{agent_name}}</p>"
            + _cta("support_ticket_url", "View ticket")
        ),
    },
    "Help_request": {
        "Name": "Help request",
        "Subject": "We received your request {{ticket_id}}",
        "TextBody": "Hi {{user_name}}, we received {{ticket_subject}} ({{ticket_id}}). Status: {{ticket_status}}. {{support_ticket_url}}",
        "HtmlBody": _layout(
            "<p>Hi {{user_name}},</p>"
            "<p>We received your request <strong>{{ticket_subject}}</strong>.</p>"
            "<p>Ticket {{ticket_id}} · {{ticket_status}} · Submitted {{submitted_date}}</p>"
            "<p>Typical response time: {{response_time_estimate}}</p>"
            + _cta("support_ticket_url", "View request")
        ),
    },
    "Billing_questions": {
        "Name": "Billing questions",
        "Subject": "{{billing_heading}}",
        "TextBody": "Hi {{user_name}}, {{billing_message}} Invoice {{invoice_id}} · {{plan_name}} · {{billing_amount}} · {{billing_status}}",
        "HtmlBody": _layout(
            "<p>Hi {{user_name}},</p>"
            "<p>{{billing_message}}</p>"
            "<p><strong>{{billing_title}}</strong></p>"
            "<p>Invoice {{invoice_id}}<br>Plan: {{plan_name}}<br>Amount: {{billing_amount}}<br>Status: {{billing_status}}</p>"
            '<p><a href="{{invoice_pdf_url.invoice_pdf_url}}">Download invoice</a></p>'
        ),
    },
}


def _request(method: str, path: str, token: str, body: dict | None = None) -> tuple[int, dict]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{POSTMARK_API}{path}",
        data=data,
        method=method,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Postmark-Server-Token": token,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {"Message": raw}
        return exc.code, payload


def _existing_by_alias(token: str) -> dict[str, int]:
    found: dict[str, int] = {}
    offset = 0
    while True:
        status, payload = _request(
            "GET",
            f"/templates?count=100&offset={offset}&templateType=Standard",
            token,
        )
        if status != 200:
            raise SystemExit(f"List templates failed ({status}): {payload}")
        templates = payload.get("Templates") or []
        for row in templates:
            alias = row.get("Alias") or ""
            tid = row.get("TemplateId")
            if alias and tid:
                found[alias] = int(tid)
        total = int(payload.get("TotalCount") or 0)
        offset += len(templates)
        if offset >= total or not templates:
            break
    return found


def main() -> None:
    token = _token()
    existing = _existing_by_alias(token)
    created = 0
    updated = 0
    for alias in ALIASES:
        spec = TEMPLATES[alias]
        body = {
            "Name": spec["Name"],
            "Alias": alias,
            "Subject": spec["Subject"],
            "HtmlBody": spec["HtmlBody"],
            "TextBody": spec["TextBody"],
            "TemplateType": "Standard",
        }
        template_id = existing.get(alias)
        if template_id:
            status, payload = _request("PUT", f"/templates/{template_id}", token, body)
            if status != 200:
                raise SystemExit(f"Update {alias} failed ({status}): {payload}")
            updated += 1
            print(f"updated {alias} id={payload.get('TemplateId', template_id)}")
        else:
            status, payload = _request("POST", "/templates", token, body)
            if status != 200:
                raise SystemExit(f"Create {alias} failed ({status}): {payload}")
            created += 1
            print(f"created {alias} id={payload.get('TemplateId')}")
    print(f"done created={created} updated={updated}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
