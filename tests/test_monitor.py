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


class ForkWithoutKeyOrEmail(unittest.TestCase):
    """A fresh fork has neither GEMINI_API_KEY nor SMTP: the run must still pass."""

    def test_new_and_outdated_articles_keep_run_green(self):
        seen = {"https://a.example/old": {"title": "Old", "text": "t", "date": "Oct 01, 2026"}}  # no rules_version
        saved = {}
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.multiple(
            monitor,
            fetch=mock.Mock(return_value=""),
            discover_articles=mock.Mock(return_value=["https://a.example/new"]),
            extract_article=mock.Mock(return_value=("Introducing Claude 9", "model release", "Oct 26, 2026", False)),
            load_seen_data=mock.Mock(return_value=seen),
            save_seen_data=mock.Mock(side_effect=lambda d: saved.update(d)),
            update_index_html=mock.Mock(),
            write_feed=mock.Mock(),
        ):
            categorize.reset_verifier_failures()
            self.assertEqual(monitor.main(), 0)
            self.assertEqual(categorize.verifier_failures(), [])
        self.assertEqual(saved["https://a.example/new"]["category_source"], "rules")
        self.assertEqual(saved["https://a.example/old"].get("rules_version"), monitor.RULES_VERSION)


class CategoryRules(unittest.TestCase):
    """Real newsroom titles from seen_data.json; catches regressions when CATEGORY_RULES change."""

    TITLES = {
        "Policy & Safety": ["2026 Usage Policy update",
                            "Announcing our updated Responsible Scaling Policy"],
        "Org & Leadership": ["Physicist Christopher Stubbs joins Anthropic's Long-Term Benefit Trust",
                             "Anthropic appoints KiYoung Choi as Representative Director of Korea ahead of Seoul office opening",
                             "Anthropic names Theo Hourmouzis General Manager of Australia & New Zealand and officially opens Sydney office"],
        "Cybersecurity & Safeguards": ["Developing Enterprise Frontier Safeguards with our customers",
                                       "Safeguards",
                                       "Government of Alberta uses Claude to find and fix cybersecurity vulnerabilities across government systems"],
        "Transparency & Reports": ["Results from the first Anthropic Public Record"],
        "Product Launch": ["Introducing Claude Design by Anthropic Labs",
                           "Introducing Claude for Small Business",
                           "Claude for Creative Work"],
        "Model Release": ["Introducing Claude Opus 4.7",
                          "Introducing Claude Sonnet 5",
                          "Claude Fable 5 and Claude Mythos 5"],
        "Infrastructure & Compute": ["Anthropic and Amazon expand collaboration for up to 5 gigawatts of new compute",
                                     "Anthropic expands partnership with Google and Broadcom for multiple gigawatts of next-generation compute",
                                     "Higher usage limits for Claude and a compute deal with SpaceX"],
        "Government & Region": ["Anthropic opens Singapore office and signs MOU with the Government of Singapore",
                                "Anthropic opens Milan office to support Italian enterprise, research, and developers",
                                "Anthropic opens Seoul office and announces new partnerships across the Korean AI ecosystem"],
        "Acquisition": ["Anthropic acquires Stainless"],
        "Investment & Funding": ["Anthropic files S-1 with the SEC for proposed initial public offering",
                                 "Anthropic raises $65B in Series H funding at $965B post-money valuation",
                                 "Anthropic confidentially submits draft S-1 to the SEC"],
        "Enterprise Deployment": ["PwC is deploying Claude to build technology, execute deals, and reinvent enterprise functions for clients",
                                  "KPMG integrates Claude across its core business and workforce of more than 276,000 in strategic alliance"],
        "Partner Network & Ecosystem": ["Introducing the Services Track and Partner Hub of the Claude Partner Network",
                                        "TCS and Anthropic partner to bring Claude to regulated industries",
                                        "Cognizant and Anthropic expand their partnership to bring Claude to enterprise clients"],
        "Research & Institute": ["Introducing The Anthropic Institute",
                                 "A research agenda for the Economic Futures Research Fund"],
        "Brand & Vision": ["Claude is a space to think"],
    }

    def test_every_category_is_covered(self):
        self.assertEqual(sorted(self.TITLES), sorted(categorize.CATEGORIES))

    def test_titles_match_by_title_alone(self):
        for category, titles in self.TITLES.items():
            for title in titles:
                with self.subTest(title=title):
                    self.assertEqual(categorize.categorize_by_rules(title, ""), (category, 1.0))

    def test_no_match_is_uncategorized(self):
        self.assertEqual(categorize.categorize_by_rules("Hello", "Nothing to see."), ("Uncategorized", 0.0))


class LayoutChange(unittest.TestCase):
    def test_no_article_links_fails_run_without_saving(self):
        with mock.patch.multiple(
            monitor,
            fetch=mock.Mock(return_value="<html><body>redesigned</body></html>"),
            save_seen_data=mock.Mock(),
            update_index_html=mock.Mock(),
            write_feed=mock.Mock(),
        ):
            self.assertEqual(monitor.main(), 1)
            monitor.save_seen_data.assert_not_called()


if __name__ == "__main__":
    unittest.main()
