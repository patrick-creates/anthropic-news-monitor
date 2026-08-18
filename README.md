# 📬 Anthropic News Monitor
[![Maintained by Telosignal](https://img.shields.io/badge/Maintained%20by-Telosignal-green)](https://www.telosignal.com/)
[![Python Version](https://img.shields.io/badge/python-3.9%2B-blue)](https://www.python.org/)
[![GitHub Pages](https://img.shields.io/badge/Live-News_Feed-green)](https://patrick-creates.github.io/anthropic-news-monitor/)
[![Anthropic News Monitor](https://github.com/patrick-creates/anthropic-news-monitor/actions/workflows/monitor.yml/badge.svg?branch=main)](https://github.com/patrick-creates/anthropic-news-monitor/actions/workflows/monitor.yml)

**Stay ahead of the AI curve.** The Anthropic News Monitor is an automated, lightweight Python tool that tracks the official Anthropic newsroom, categorizes every post, publishes a browsable dashboard, and emails you the moment something new lands.

Built and maintained by [Telosignal](https://www.telosignal.com/).

---

<p align="center">
  <a href="https://patrick-creates.github.io/anthropic-news-monitor/">
    <kbd>
      <img width="800" src="https://github.com/user-attachments/assets/36fb829b-0ced-4b36-923e-5a1ee37db3b6" alt="Intelligence Feed Dashboard Preview">
    </kbd>
  </a>
</p>

---

## ✨ Features

* **📰 Targeted Scraping:** Monitors `anthropic.com/news` for high-signal updates.
* **🏷️ Automatic Categorization:** Rule-based labelling with an optional LLM verifier that double-checks each guess and overrides it when the rules get it wrong.
* **🔎 Entity Extraction:** Surfaces the companies, regions, products, and programs mentioned in each post.
* **📊 Live Dashboard:** Regenerates `index.html` on every run — category cadence, entity chips, and clickable filters — published via GitHub Pages.
* **✉️ Direct Email Alerts:** Sends formatted notifications via SMTP.
* **🛡️ Duplicate Prevention:** Keeps a record of seen articles in `seen_data.json` so you're never alerted twice.
* **☁️ Runs Anywhere:** One-shot script — run it from GitHub Actions, a cron entry, or by hand.

---

## 🎯 What It Tracks

Every article is sorted into one of these categories:

| | |
|---|---|
| Model Release | Product Launch |
| Cybersecurity & Safeguards | Transparency & Reports |
| Policy & Safety | Government & Region |
| Infrastructure & Compute | Enterprise Deployment |
| Partner Network & Ecosystem | Investment & Funding |
| Research & Institute | Org & Leadership |
| Acquisition | Brand & Vision |

Anything the rules can't place is labelled `Uncategorized`.

---

## 🚀 Quick Start

### Prerequisites
* Python 3.9 or higher (CI runs 3.12)
* An SMTP-enabled email account (e.g. Gmail with an **App Password**)
* Optional: an API key for the category verifier (see [Verifier](#-verifier))

### Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/patrick-creates/anthropic-news-monitor.git
   cd anthropic-news-monitor
   ```

2. **Install dependencies:**
   *(a virtual environment is recommended)*
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure your environment:**

   These are read directly from the environment — export them, or use a `.env` loader of your choice.

   ```env
   SMTP_HOST=smtp.gmail.com
   SMTP_PORT=587
   SMTP_USER=your-email@gmail.com
   SMTP_PASS=your-app-password
   TO_EMAIL=target-email@example.com

   # Optional — enables the category verifier
   GEMINI_API_KEY=your-api-key
   ```

   > **Note on Gmail:** with 2-Factor Authentication enabled you must generate an [App Password](https://support.google.com/accounts/answer/185833?hl=en) and use it as `SMTP_PASS`.

### Usage

```bash
python monitor.py
```

Each invocation fetches the newsroom once, processes anything new, rewrites `index.html`, and exits. Schedule it with cron or GitHub Actions for continuous monitoring — there is no polling loop.

---

## 🔎 Verifier

Categories come from a fast, free, deterministic rule pass. When an API key is present, each result is also checked by an LLM, which can override the rules and records why.

The verifier is **optional**. Without a key the monitor still works; articles are simply labelled `rules` instead of `rules+verified`.

Any OpenAI-compatible `/chat/completions` endpoint works. Defaults target Gemini:

**Set these in CI** — the key as a repository *secret*, the rest as repository *variables*:

| Name | Default | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | — | API key (secret). Without it the verifier is skipped. |
| `VERIFIER_MODEL` | `gemini-3.6-flash` | Model name. |
| `VERIFIER_MIN_INTERVAL` | `4` | Seconds between requests. Raise it to fit a per-minute quota — `60 / your_RPM`. |
| `VERIFIER_MAX_TOKENS` | `2000` | Response budget. Reasoning models need headroom or they return empty content. |

**Local overrides only** — these exist for testing against a mock server or trying another provider. They are deliberately not wired into the workflow, because switching provider generally means editing the request shape in `verify_with_llm` anyway:

| Name | Default | Purpose |
|---|---|---|
| `VERIFIER_ENDPOINT` | Gemini's OpenAI-compatible URL | Any OpenAI-compatible `/chat/completions` URL. |
| `VERIFIER_KEY_ENV` | `GEMINI_API_KEY` | Which environment variable holds the key. |

**Free tiers are usually rate-limited.** If you see `HTTP 429`, raise `VERIFIER_MIN_INTERVAL` to match your requests-per-minute allowance, and lower `MAX_RECATEGORIZE_PER_RUN` in `monitor.py` if you're also near a daily cap.

### Re-categorizing

`categorize.py` doubles as a CLI for relabelling the whole archive — useful after changing the rules:

```bash
python categorize.py                  # rules + verifier
python categorize.py --no-verify      # rules only, zero API calls
python categorize.py --allow-degraded # write even if the verifier failed
```

By default, if the verifier is unreachable the run **aborts without writing**, so a dead API key can't quietly downgrade labels that were previously verified.

Bump `RULES_VERSION` in `categorize.py` after editing the rules. `monitor.py` re-labels anything carrying an older version, a few articles per run, so changes propagate without a manual step.

---

## 🤖 Automatic Monitoring (GitHub Actions)

The included workflow runs daily at 14:00 UTC.

1. **Add repository secrets**
   **Settings → Secrets and variables → Actions → Secrets**
   * `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `TO_EMAIL`
   * `GEMINI_API_KEY` — optional, enables the verifier

2. **Add repository variables** *(optional)*
   **Settings → Secrets and variables → Actions → Variables**
   * `VERIFIER_MODEL` — e.g. `gemini-3.6-flash`
   * `VERIFIER_MIN_INTERVAL` — e.g. `15` for a 5 requests/minute tier
   * `VERIFIER_MAX_TOKENS` — e.g. `8000`

   Variables, not secrets — secret values are masked in logs, which would redact the model name from the very error message telling you it's wrong.

3. **Enable permissions**
   The workflow commits `seen_data.json` and `index.html`. Under **Settings → Actions → General → Workflow permissions**, select **"Read and write permissions"**.

4. **Run it**
   Daily on schedule, or manually from the **Actions** tab. The manual trigger offers a **Re-apply category rules** checkbox, which runs `categorize.py --no-verify` over the whole archive before the normal monitoring step — leave it unchecked for ordinary runs.

The job exits non-zero when the verifier fails, so a broken key or retired model shows up as a red build rather than silently degrading.

---

## 📸 Example Output

When a new post is detected you'll receive an email like:

> **Subject:** [Anthropic News] [Model Release] Introducing Claude Opus 5
>
> Source: https://www.anthropic.com/news/claude-opus-5
> Published: Jul 24, 2026
> Category: Model Release (via rules+verified)

---

## 🗂️ Repository Layout

| File | Purpose |
|---|---|
| `monitor.py` | Scrapes, emails, renders `index.html`. Entry point. |
| `categorize.py` | Category rules, entity patterns, LLM verifier. Also a CLI. |
| `template.html` | Dashboard shell that `monitor.py` fills in. |
| `styles.css` | Dashboard styling, linked from `template.html`. |
| `seen_data.json` | Article archive — text, dates, categories, entities. |
| `index.html` | Generated dashboard. Do not edit by hand. |

---

## 📄 License

Distributed under the MIT License. See `LICENSE` for more information.
