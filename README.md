# Anthropic News Monitor

Daily GitHub Actions job that scrapes
[anthropic.com/news](https://www.anthropic.com/news), emails the full text
of each new article via SMTP, and commits tracked URLs back to
`seen.json`.

## Setup

1. Push this repo to GitHub and merge to `main` (scheduled workflows only
   fire on the default branch).
2. In **Settings → Secrets and variables → Actions**, add:
   - `SMTP_HOST` — e.g. `smtp.gmail.com`
   - `SMTP_PORT` — e.g. `587`
   - `SMTP_USER` — SMTP username (also used as the `From` address)
   - `SMTP_PASS` — SMTP password or app password
   - `TO_EMAIL`  — recipient address
3. (Optional) **Actions → Anthropic News Monitor → Run workflow** to
   trigger immediately. The first run seeds `seen.json` silently so the
   back-catalog is not dumped into your inbox; subsequent runs email
   only newly published articles.

## Schedule

Runs daily at 14:00 UTC. Edit the `cron` field in
`.github/workflows/monitor.yml` to change.

## Files

- `monitor.py` — scrape, diff, email, persist.
- `requirements.txt` — `requests` + `beautifulsoup4`.
- `.github/workflows/monitor.yml` — the scheduled job.
- `seen.json` — created automatically; tracks emailed URLs.
