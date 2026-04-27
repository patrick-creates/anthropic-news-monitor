"""Monitor https://www.anthropic.com/news and email any new articles."""

from __future__ import annotations

import json
import os
import smtplib
import sys
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from datetime import datetime

NEWS_URL = "https://www.anthropic.com/news"
SEEN_FILE = Path(__file__).parent / "seen.json"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)
TIMEOUT = 30


def fetch(url: str) -> str:
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.text


def discover_articles(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    urls: list[str] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].split("#", 1)[0].split("?", 1)[0]
        absolute = urljoin(NEWS_URL, href).rstrip("/")
        prefix = "https://www.anthropic.com/news/"
        if not absolute.startswith(prefix):
            continue
        slug = absolute[len(prefix):].strip("/")
        if not slug:
            continue
        if absolute not in seen:
            seen.add(absolute)
            urls.append(absolute)
    return urls


def extract_article(html: str) -> tuple[str, str]:
    soup = BeautifulSoup(html, "html.parser")

    title_el = soup.find("h1") or soup.find("title")
    title = title_el.get_text(strip=True) if title_el else "Anthropic News"

    body_el = soup.find("article") or soup.find("main") or soup.body
    if body_el is None:
        text = soup.get_text("\n", strip=True)
    else:
        for tag in body_el.find_all(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        text = body_el.get_text("\n", strip=True)

    return title, text


def load_seen() -> set[str] | None:
    if not SEEN_FILE.exists():
        return None
    raw = SEEN_FILE.read_text().strip()
    if not raw:
        return None
    return set(json.loads(raw))


def save_seen(urls: set[str]) -> None:
    SEEN_FILE.write_text(json.dumps(sorted(urls), indent=2) + "\n")


def send_email(subject: str, body: str) -> None:
    host = os.environ.get("SMTP_HOST") or "smtp.gmail.com"
    port = int(os.environ.get("SMTP_PORT") or "587")
    user = os.environ["SMTP_USER"]
    password = os.environ["SMTP_PASS"]
    to_addr = os.environ["TO_EMAIL"]

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to_addr
    msg.set_content(body)

    with smtplib.SMTP(host, port, timeout=TIMEOUT) as smtp:
        smtp.starttls()
        smtp.login(user, password)
        smtp.send_message(msg)


def update_index_html(all_articles: list):
    """Creates a simple website showing all news found so far."""
    html_template = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Anthropic News Monitor</title>
        <style>
            body { font-family: sans-serif; max-width: 800px; margin: 40px auto; padding: 20px; line-height: 1.6; }
            h1 { color: #1a1a1a; border-bottom: 2px solid #eee; }
            li { margin-bottom: 10px; }
            .date { color: #666; font-size: 0.8em; }
        </style>
    </head>
    <body>
        <h1>Latest Anthropic News</h1>
        <ul>
            {list_items}
        </ul>
        <hr>
        <p class="date">Last checked: {last_updated}</p>
    </body>
    </html>
    """
    
    # Generate the <li> list items for the HTML
    items = ""
    for url in reversed(all_articles): # Show newest first
        title = url.split('/')[-1].replace('-', ' ').title()
        items += f"<li><a href='{url}' target='_blank'>{title}</a></li>\n"
    
    # Combine everything
    final_html = html_template.format(
        list_items=items, 
        last_updated=datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
    )
    
    # Save the file
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(final_html)
    print("Successfully updated index.html for GitHub Pages.")

def main() -> int:
    print(f"Fetching {NEWS_URL}")
    listing = fetch(NEWS_URL)
    current = discover_articles(listing)
    print(f"Found {len(current)} article link(s) on listing page")

    seen = load_seen()
    if seen is None:
        print("First run: seeding seen.json without sending emails")
        save_seen(set(current))
        return 0

    new_urls = [u for u in current if u not in seen]
    print(f"{len(new_urls)} new article(s)")

    for url in new_urls:
        print(f"Processing {url}")
        html = fetch(url)
        title, text = extract_article(html)
        body = f"{url}\n\n{text}\n"
        send_email(f"[Anthropic News] {title}", body)
        seen.add(url)
        save_seen(seen)
        print(f"  emailed: {title}")

    seen_articles = load_seen()
    if seen_articles:
        update_index_html(list(seen_articles))

    return 0


if __name__ == "__main__":
    sys.exit(main())
