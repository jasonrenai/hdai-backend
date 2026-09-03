"""User-facing Postmark failure messages used by forgot-password."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.email.helpers import describe_email_send_failure


class TestDescribeEmailSendFailure(unittest.TestCase):
    def test_disabled(self):
        self.assertIn("EMAIL_SENDING_ENABLED", describe_email_send_failure(disabled=True))

    def test_account_token(self):
        msg = describe_email_send_failure(Exception("401 Unauthorized: invalid token"))
        self.assertIn("Server API token", msg)

    def test_missing_template(self):
        msg = describe_email_send_failure(Exception("Template not found"))
        self.assertIn("sync_postmark_templates.py", msg)

    def test_sender(self):
        msg = describe_email_send_failure(Exception("The 'From' address is not recognized"))
        self.assertIn("hello@speakerpitcher.ai", msg)


if __name__ == "__main__":
    unittest.main()
