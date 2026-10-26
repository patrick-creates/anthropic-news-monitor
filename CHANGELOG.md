# Changelog

All notable changes to the monitor code and docs are documented here. Daily data commits by `github-actions[bot]` are not listed. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- `feed.xml`: RSS 2.0 feed with the newest 30 posts, written on every run and linked from the dashboard.
- `tests/test_monitor.py`: tests for optional email, the provider error text on retries, the feed and the re-categorization queue (`python -m unittest discover tests`).
- With a verifier key, articles labelled by the rules alone are verified later, three per run, after any articles from an older ruleset.

### Changed
- Email alerts are optional: without `SMTP_USER`, `SMTP_PASS` and `TO_EMAIL` the monitor skips sending instead of crashing.
- On 429/5xx the verifier error now carries the provider's message, not just the status code.

### Fixed
- Without `GEMINI_API_KEY` every new article counted as a verifier failure, so the run went red and articles from an older ruleset never moved on. The verifier is now only called when a key is set, as the README already promised.
- The dashboard was not published when the verifier failed: `monitor.py` exits 1 by design, which also skipped the Pages deploy, so the site stayed on old data although new articles were committed. Upload and deploy now run unless the workflow is cancelled.

## [1.0.0] – 2026-10-25

First tagged release.

### Added
- `monitor.py`: one-shot scrape of `anthropic.com/news`, email alert via SMTP for new posts, duplicate prevention via `seen_data.json`.
- `categorize.py`: rule-based categories and entity extraction, optional LLM verifier (any OpenAI-compatible endpoint, Gemini by default), `RULES_VERSION`-based gradual re-labelling, CLI for re-categorizing the archive.
- Dashboard `index.html` (from `template.html` + `styles.css`) on GitHub Pages: category cadence, entity chips, year/month/category filters.
- `monitor.yml`: daily run at 14:00 UTC, manual run with optional "Re-apply category rules"; fails the build when the verifier fails.
- CodeQL workflow, issue templates.
