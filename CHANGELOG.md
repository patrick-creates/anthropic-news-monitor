# Changelog

All notable changes to the monitor code and docs are documented here. Daily data commits by `github-actions[bot]` are not listed. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [1.0.0] – unreleased

First tagged release.

### Added
- `monitor.py`: one-shot scrape of `anthropic.com/news`, email alert via SMTP for new posts, duplicate prevention via `seen_data.json`.
- `categorize.py`: rule-based categories and entity extraction, optional LLM verifier (any OpenAI-compatible endpoint, Gemini by default), `RULES_VERSION`-based gradual re-labelling, CLI for re-categorizing the archive.
- Dashboard `index.html` (from `template.html` + `styles.css`) on GitHub Pages: category cadence, entity chips, year/month/category filters.
- `monitor.yml`: daily run at 14:00 UTC, manual run with optional "Re-apply category rules"; fails the build when the verifier fails.
- CodeQL workflow, issue templates.
