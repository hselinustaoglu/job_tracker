import os
import unittest
from unittest.mock import patch

from job_tracker.models import JobPosting
from job_tracker.notifications import EmailConfigError, maybe_send_email_alert


class NotificationTests(unittest.TestCase):
    def test_missing_email_env_raises(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with patch("job_tracker.notifications._load_dotenv"):
                with self.assertRaises(EmailConfigError):
                    maybe_send_email_alert([], [], "summary")

    def test_email_is_sent_when_env_is_present(self) -> None:
        env = {
            "SMTP_HOST": "smtp.example.com",
            "SMTP_PORT": "587",
            "SMTP_USERNAME": "user",
            "SMTP_PASSWORD": "pass",
            "ALERT_FROM_EMAIL": "sender@example.com",
            "ALERT_TO_EMAIL": "receiver@example.com",
            "SMTP_STARTTLS": "true",
        }
        jobs = [JobPosting(job_id="1", title="Data Analyst", url="https://example.com")]
        with patch.dict(os.environ, env, clear=True):
            with patch("job_tracker.notifications.smtplib.SMTP") as smtp_cls:
                maybe_send_email_alert(jobs, [], "summary")
                smtp = smtp_cls.return_value.__enter__.return_value
                smtp.starttls.assert_called_once()
                smtp.login.assert_called_once_with("user", "pass")
                smtp.send_message.assert_called_once()


if __name__ == "__main__":
    unittest.main()
