"""Run: python -m unittest discover tests"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import categorize  # noqa: E402
import monitor  # noqa: E402

SMTP = {"SMTP_USER": "u", "SMTP_PASS": "p", "TO_EMAIL": "t"}


class EmailIsOptional(unittest.TestCase):
    def test_skips_without_settings(self):
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch("smtplib.SMTP") as smtp:
            monitor.send_email("subject", "body")
            smtp.assert_not_called()

    def test_skips_when_one_setting_missing(self):
        env = {k: v for k, v in SMTP.items() if k != "TO_EMAIL"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch("smtplib.SMTP") as smtp:
            monitor.send_email("subject", "body")
            smtp.assert_not_called()

    def test_sends_when_configured(self):
        with mock.patch.dict(os.environ, SMTP, clear=True), mock.patch("smtplib.SMTP") as smtp:
            monitor.send_email("subject", "body")
            smtp.return_value.__enter__.return_value.send_message.assert_called_once()


class RetryErrorKeepsReason(unittest.TestCase):
    def test_503_message_includes_provider_text(self):
        resp = mock.Mock(status_code=503, headers={})
        resp.json.return_value = {"error": {"message": "The model is overloaded."}}
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "k"}), \
                mock.patch.object(categorize.requests, "post", return_value=resp), \
                mock.patch.object(categorize.time, "sleep"):
            categorize.reset_verifier_failures()
            with self.assertRaises(categorize.VerifierUnavailable) as ctx:
                categorize.verify_with_llm("title", "body", "Model Release")
        self.assertIn("The model is overloaded.", str(ctx.exception))
        self.assertIn("HTTP 503", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
