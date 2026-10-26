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


class Feed(unittest.TestCase):
    def test_valid_rss_newest_first_and_escaped(self):
        import tempfile
        import xml.etree.ElementTree as ET
        from pathlib import Path
        data = {
            "https://a.example/old": {"title": "Old", "date": "Jan 02, 2026", "category": "Policy & Safety", "text": "x"},
            "https://a.example/new": {"title": "New <b> & co", "date": "Oct 20, 2026", "text": "  body \n text "},
            "https://a.example/nodate": {"title": "Undated", "date": "Recent"},
        }
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(monitor, "FEED_FILE", Path(tmp) / "feed.xml"):
            monitor.write_feed(data)
            root = ET.parse(Path(tmp) / "feed.xml").getroot()  # raises if not well-formed
        items = root.findall("./channel/item")
        self.assertEqual([i.findtext("title") for i in items], ["New <b> & co", "Old", "Undated"])
        self.assertEqual(items[0].findtext("description"), "body text")
        self.assertEqual(items[0].findtext("category"), "Uncategorized")
        self.assertTrue(items[0].findtext("pubDate").startswith("Tue, 20 Oct 2026"))
        self.assertIsNone(items[2].find("pubDate"))


class Recategorize(unittest.TestCase):
    data = {
        "verified": {"rules_version": monitor.RULES_VERSION, "category_source": "rules+verified"},
        "override": {"rules_version": monitor.RULES_VERSION, "category_source": "verifier_override"},
        "rules-only": {"rules_version": monitor.RULES_VERSION, "category_source": "rules"},
        "old-rules": {"rules_version": monitor.RULES_VERSION - 1, "category_source": "rules+verified"},
        "never": {},
    }

    def test_without_key_only_outdated_rules(self):
        self.assertEqual(monitor.needs_recategorize(self.data, verify=False), ["old-rules", "never"])

    def test_with_key_unverified_follow_outdated(self):
        self.assertEqual(monitor.needs_recategorize(self.data, verify=True),
                         ["old-rules", "never", "rules-only"])


if __name__ == "__main__":
    unittest.main()
