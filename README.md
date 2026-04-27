# 📬 Anthropic News Monitor
[![Maintained by Telosignal](https://img.shields.io/badge/Maintained%20by-Telosignal-green)](https://www.telosignal.com/)
[![Python Version](https://img.shields.io/badge/python-3.9%2B-blue)](https://www.python.org/)
[![GitHub Pages](https://img.shields.io/badge/Live-News_Feed-green)](https://patrick-creates.github.io/anthropic-news-monitor/)
[![Monitor Status](https://github.com/patrick-creates/anthropic-news-monitor/actions/workflows/monitor.yml/badge.svg)](https://github.com/patrick-creates/anthropic-news-monitor/actions)

**Stay ahead of the AI curve.** The Anthropic News Monitor is an automated, lightweight Python tool that tracks the official Anthropic newsroom and delivers instant email alerts the moment a new model, research paper, or product update is published. 

Built and maintained by [Telosignal](https://www.telosignal.com/).

---

## ✨ Features

* **📰 Targeted Scraping:** Specifically monitors `anthropic.com/news` for high-signal updates.
* **✉️ Direct Email Alerts:** Sends beautifully formatted email notifications via SMTP directly to your inbox.
* **🛡️ Duplicate Prevention:** Maintains a local record of seen articles so you never get spammed with the same update twice.
* **☁️ Deployment Ready:** Designed to run effortlessly on a local server, Raspberry Pi, or as a scheduled CRON job.

---

## 🎯 What It Tracks

This monitor is designed to alert you on critical updates in the Anthropic ecosystem, including:
1. **Model Releases** (e.g., Claude 3.5 Sonnet, Opus)
2. **Product Updates** (e.g., Artifacts, Workbench)
3. **Research & Science** (AI safety, alignment papers)
4. **API & Developer Tooling**

---

## 🚀 Quick Start

### Prerequisites
* Python 3.9 or higher
* An SMTP-enabled email account (e.g., a Gmail account using an **App Password**)

### Installation

1. **Clone the repository:**
   ```bash
   git clone [https://github.com/patrick-creates/anthropic-news-monitor.git](https://github.com/patrick-creates/anthropic-news-monitor.git)
   cd anthropic-news-monitor
   ```

2. **Install the required dependencies:**
   *(It is recommended to use a virtual environment)*
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure your environment:**
   Create a `.env` file in the root directory and add your mail server credentials:
   ```env
   # .env
   SMTP_SERVER=smtp.gmail.com
   SMTP_PORT=587
   SENDER_EMAIL=your-email@gmail.com
   SENDER_PASSWORD=your-app-password
   RECEIVER_EMAIL=target-email@example.com
   
   # Optional: How often to check for updates (in seconds)
   CHECK_INTERVAL=3600
   ```
   > **Note on Gmail:** If you have 2-Factor Authentication enabled, you must generate an [App Password](https://support.google.com/accounts/answer/185833?hl=en) to use as your `SENDER_PASSWORD`.

### Usage

To start the monitor, simply run:
```bash
python main.py
```
*(Leave this running in the background, or set it up as a system service/cron job for continuous monitoring).*

---

## 🤖 Automatic Monitoring (GitHub Actions)

This repository includes a built-in GitHub Action that checks for news automatically every day at 14:00 UTC. To enable this for your fork:

1. **Set up GitHub Secrets:**
   Go to your repo **Settings > Secrets and variables > Actions** and add the following repository secrets:
   * `SMTP_HOST`: e.g., `smtp.gmail.com`
   * `SMTP_PORT`: e.g., `587`
   * `SMTP_USER`: Your email address
   * `SMTP_PASS`: Your App Password
   * `TO_EMAIL`: The address where you want to receive alerts

2. **Enable Permissions:**
   The workflow needs permission to update `seen.json`. 
   Go to **Settings > Actions > General**, scroll to **Workflow permissions**, and select **"Read and write permissions"**.

3. **Profit:**
   The bot will now run daily. You can also trigger it manually from the **Actions** tab.

---

## 📸 Example Output

When a new post is detected, you will receive an email formatted like this:

> **Subject:** 🤖 New Anthropic Update: Claude 3.5 Sonnet
> 
> A new post has been published in the Anthropic Newsroom.
> 
> **Title:** Claude 3.5 Sonnet is now available
> **Link:** https://www.anthropic.com/news/claude-3-5-sonnet

---

## 📄 License

Distributed under the MIT License. See `LICENSE` for more information.
