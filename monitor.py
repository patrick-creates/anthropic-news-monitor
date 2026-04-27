"""Monitor https://www.anthropic.com/news and email any new articles."""

from __future__ import annotations

import json
import os
import re 
import smtplib
import sys
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from datetime import datetime

NEWS_URL = "https://www.anthropic.com/news"
SEEN_FILE = Path(__file__).parent / "seen_data.json"
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


def extract_article(html: str) -> tuple[str, str, str]:
    """Extracts title, FULL text, and published date using Regex fix."""
    soup = BeautifulSoup(html, "html.parser")

    # 1. Extract Title
    title_el = soup.find("h1") or soup.find("title")
    title = title_el.get_text(strip=True) if title_el else "Anthropic News"

    # 2. Extract Date (REGEX FIX from your successful local test)
    date_str = "Recent"
    date_pattern = re.compile(
        r'(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},\s+20\d{2}', 
        re.IGNORECASE
    )
    
    # Scan all small text blocks for the date pattern
    for element in soup.find_all(['div', 'p', 'span']):
        text_val = element.get_text(strip=True)
        if 10 < len(text_val) < 50:
            match = date_pattern.search(text_val)
            if match:
                date_str = match.group(0)
                break

    # 3. Extract Body Content (Full text preserved for email)
    body_el = soup.find("article") or soup.find("main") or soup.body
    if body_el is None:
        text = soup.get_text("\n", strip=True)
    else:
        # Create a copy so we don't mess up the original soup
        import copy
        content = copy.copy(body_el)
        # Remove fluff for the content extraction
        for tag in content.find_all(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        text = content.get_text("\n", strip=True)

    return title, text, date_str


def load_seen_data() -> dict:
    if not SEEN_FILE.exists():
        return {}
    try:
        return json.loads(SEEN_FILE.read_text())
    except:
        return {}


def save_seen_data(data: dict) -> None:
    SEEN_FILE.write_text(json.dumps(data, indent=2) + "\n")


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


def update_index_html(articles_data: dict):
    try:
        with open("template.html", "r") as f:
            html_template = f.read()
    except FileNotFoundError:
        return

    # Helper to convert "Apr 16, 2026" into a sortable object
    def get_date(item):
        try:
            return datetime.strptime(item[1]['date'], "%b %d, %Y")
        except:
            return datetime.min

    # Sort: Newest articles at the top
    sorted_items = sorted(articles_data.items(), key=get_date, reverse=True)

    items = ""
    for url, info in sorted_items:
        snippet = " ".join(info['text'].replace('\n', ' ').split()[:40]) + "..."
        
        # We add 'data-year' and 'data-month' for the JS filter
        try:
            dt = datetime.strptime(info['date'], "%b %d, %Y")
            year = dt.strftime("%Y")
            month = dt.strftime("%m")
        except:
            year = "unknown"
            month = "unknown"

        items += f"""
        <li class="post-item" data-year="{year}" data-month="{month}">
            <span class="post-date">{info['date']}</span>
            <a href="{url}" target="_blank" class="post-title">{info['title']}</a>
            <p class="post-snippet">{snippet}</p>
        </li>\n"""
    
    final_html = html_template.format(
        articles=items, 
        last_updated=datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
    )
    
    with open("index.html", "w") as f:
        f.write(final_html)


def main() -> int:
    print(f"Fetching {NEWS_URL}")
    listing = fetch(NEWS_URL)
    current_urls = discover_articles(listing)
    
    seen_data = load_seen_data()

    if not seen_data:
        print("First run: seeding metadata...")
        for url in current_urls:
            html = fetch(url)
            title, text, date_str = extract_article(html)
            seen_data[url] = {"title": title, "text": text, "date": date_str}
        save_seen_data(seen_data)
        update_index_html(seen_data)
        return 0

    new_count = 0
    for url in current_urls:
        if url not in seen_data:
            print(f"Processing new article: {url}")
            html = fetch(url)
            title, text, date_str = extract_article(html)
            
            # Send email with FULL text
            email_body = f"Source: {url}\nPublished: {date_str}\n\n{text}"
            send_email(f"[Anthropic News] {title}", email_body)
            
            seen_data[url] = {"title": title, "text": text, "date": date_str}
            new_count += 1
            save_seen_data(seen_data)

    print(f"Found {new_count} new article(s).")
    update_index_html(seen_data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
