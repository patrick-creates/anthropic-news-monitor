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
from datetime import datetime, timedelta
from string import Template
from collections import Counter, defaultdict

from categorize import categorize_article, CATEGORIES

NEWS_URL = "https://www.anthropic.com/news"
SEEN_FILE = Path(__file__).parent / "seen_data.json"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)
TIMEOUT = 30

# Color palette for category badges/chips. Keep keys aligned with categorize.py.
CATEGORY_COLORS: dict[str, str] = {
    "Model Release": "#7c3aed",
    "Product Launch": "#0891b2",
    "Infrastructure & Compute": "#ea580c",
    "Enterprise Deployment": "#0d9488",
    "Investment & Funding": "#16a34a",
    "Acquisition": "#15803d",
    "Partner Network & Ecosystem": "#65a30d",
    "Policy & Safety": "#dc2626",
    "Government & Region": "#9333ea",
    "Org & Leadership": "#ca8a04",
    "Research & Institute": "#2563eb",
    "Brand & Vision": "#db2777",
    "Uncategorized": "#6b7280",
}


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

    title_el = soup.find("h1") or soup.find("title")
    title = title_el.get_text(strip=True) if title_el else "Anthropic News"

    date_str = "Recent"
    date_pattern = re.compile(
        r'(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},\s+20\d{2}',
        re.IGNORECASE
    )
    for element in soup.find_all(['div', 'p', 'span']):
        text_val = element.get_text(strip=True)
        if 10 < len(text_val) < 50:
            match = date_pattern.search(text_val)
            if match:
                date_str = match.group(0)
                break

    body_el = soup.find("article") or soup.find("main") or soup.body
    if body_el is None:
        text = soup.get_text("\n", strip=True)
    else:
        import copy
        content = copy.copy(body_el)
        for tag in content.find_all(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        text = content.get_text("\n", strip=True)

    return title, text, date_str


def load_seen_data() -> dict:
    if not SEEN_FILE.exists():
        return {}
    try:
        return json.loads(SEEN_FILE.read_text())
    except Exception:
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


# ---------------------------------------------------------------------------
# Rendering: trends panel, chips, badges
# ---------------------------------------------------------------------------

def _parse_date(date_str: str) -> datetime:
    try:
        return datetime.strptime(date_str, "%b %d, %Y")
    except Exception:
        return datetime.min


def build_trends(articles_data: dict) -> dict:
    """Compute the numbers shown in the trends panel.

    Returns a dict with:
      - category_counts: {category: total count}
      - category_last_seen_days: {category: days since last article of that category}
      - last_90_counts: {category: count in last 90 days}
      - top_entities: list of {name, type, count} for most-mentioned entities
      - total: total article count
    """
    today = datetime.now()
    category_counts: Counter = Counter()
    last_90_counts: Counter = Counter()
    category_last_seen: dict[str, datetime] = {}
    entity_counts: Counter = Counter()
    entity_types: dict[str, str] = {}

    for info in articles_data.values():
        cat = info.get("category", "Uncategorized")
        category_counts[cat] += 1
        d = _parse_date(info.get("date", ""))
        if d != datetime.min:
            if cat not in category_last_seen or d > category_last_seen[cat]:
                category_last_seen[cat] = d
            if d >= today - timedelta(days=90):
                last_90_counts[cat] += 1
        for ent in info.get("entities", []):
            key = ent["name"]
            entity_counts[key] += 1
            entity_types[key] = ent["type"]

    last_seen_days = {
        cat: (today - d).days for cat, d in category_last_seen.items()
    }

    top_entities = [
        {"name": name, "type": entity_types[name], "count": n}
        for name, n in entity_counts.most_common(10)
    ]

    return {
        "category_counts": dict(category_counts),
        "category_last_seen_days": last_seen_days,
        "last_90_counts": dict(last_90_counts),
        "top_entities": top_entities,
        "total": len(articles_data),
    }


def render_trends_html(trends: dict) -> str:
    """Render the trends panel: category bars + entity chips."""
    counts = trends["category_counts"]
    last_seen = trends["category_last_seen_days"]
    last_90 = trends["last_90_counts"]
    total = trends["total"] or 1

    sorted_cats = sorted(counts.items(), key=lambda x: (-x[1], x[0]))
    max_count = max(counts.values()) if counts else 1

    bars = []
    for cat, n in sorted_cats:
        color = CATEGORY_COLORS.get(cat, "#6b7280")
        width_pct = int(n / max_count * 100)
        recent = last_90.get(cat, 0)
        gap = last_seen.get(cat)
        gap_label = f"{gap}d ago" if gap is not None else "—"
        bars.append(f"""
        <div class="trend-row">
            <div class="trend-label">
                <span class="cat-dot" style="background:{color}"></span>
                <span class="cat-name">{cat}</span>
            </div>
            <div class="trend-bar-wrap">
                <div class="trend-bar-track">
                    <div class="trend-bar" style="width:{width_pct}%;background:{color}"></div>
                </div>
                <span class="trend-count">{n}</span>
            </div>
            <div class="trend-meta">
                <span title="Articles in last 90 days">{recent} recent</span>
                <span title="Days since last article in this category">last: {gap_label}</span>
            </div>
        </div>""")

    entity_chips = []
    for ent in trends["top_entities"]:
        entity_chips.append(
            f'<span class="entity-chip" title="{ent["type"]}">'
            f'{ent["name"]} <span class="entity-count">{ent["count"]}</span></span>'
        )

    return f"""
    <details class="trends-panel" open>
        <summary class="trends-summary">SIGNAL_ANALYSIS // {total} articles tracked</summary>
        <div class="trends-grid">
            <div class="trends-col">
                <h3>Category cadence</h3>
                {''.join(bars)}
            </div>
            <div class="trends-col">
                <h3>Most-mentioned entities</h3>
                <div class="entity-chip-row">{''.join(entity_chips) or '<em>none</em>'}</div>
            </div>
        </div>
    </details>
    """


def render_chips_html(trends: dict) -> str:
    """Render the clickable category filter chips."""
    chips = ['<button class="cat-chip active" data-cat="all" onclick="filterByCat(this)">ALL</button>']
    for cat, n in sorted(trends["category_counts"].items(), key=lambda x: (-x[1], x[0])):
        color = CATEGORY_COLORS.get(cat, "#6b7280")
        # Use data attribute for filtering. Escape quotes/braces minimally.
        safe_cat = cat.replace('"', '&quot;')
        chips.append(
            f'<button class="cat-chip" data-cat="{safe_cat}" '
            f'style="--cat-color:{color}" onclick="filterByCat(this)">'
            f'{cat} <span class="chip-count">{n}</span></button>'
        )
    return "\n".join(chips)


def update_index_html(articles_data: dict) -> None:
    try:
        with open("template.html", "r") as f:
            html_template = f.read()
    except FileNotFoundError:
        return

    sorted_items = sorted(
        articles_data.items(),
        key=lambda kv: _parse_date(kv[1].get("date", "")),
        reverse=True,
    )

    items_html_parts: list[str] = []
    for url, info in sorted_items:
        snippet = " ".join(info["text"].replace("\n", " ").split()[:40]) + "..."
        d = _parse_date(info.get("date", ""))
        year = d.strftime("%Y") if d != datetime.min else "unknown"
        month = d.strftime("%m") if d != datetime.min else "unknown"
        cat = info.get("category", "Uncategorized")
        color = CATEGORY_COLORS.get(cat, "#6b7280")
        source = info.get("category_source", "rules")
        verifier_mark = " ✓" if "verified" in source else ("" if source == "rules" else " ⚙")
        safe_cat = cat.replace('"', "&quot;")

        items_html_parts.append(f"""
        <li class="post-item" data-year="{year}" data-month="{month}" data-cat="{safe_cat}">
            <div class="post-meta">
                <span class="post-date">{info.get('date','')}</span>
                <span class="cat-badge" style="background:{color}" title="source: {source}">{cat}{verifier_mark}</span>
            </div>
            <a href="{url}" target="_blank" class="post-title">{info['title']}</a>
            <p class="post-snippet">{snippet}</p>
        </li>""")

    items = "\n".join(items_html_parts)
    trends = build_trends(articles_data)
    trends_html = render_trends_html(trends)
    chips_html = render_chips_html(trends)

    t = Template(html_template)
    final_html = t.safe_substitute(
        articles=items,
        last_updated=datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC"),
        trends=trends_html,
        chips=chips_html,
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
            entry = {"title": title, "text": text, "date": date_str}
            entry.update(categorize_article(title, text))
            seen_data[url] = entry
        save_seen_data(seen_data)
        update_index_html(seen_data)
        return 0

    new_count = 0
    for url in current_urls:
        if url not in seen_data:
            print(f"Processing new article: {url}")
            html = fetch(url)
            title, text, date_str = extract_article(html)

            entry = {"title": title, "text": text, "date": date_str}
            entry.update(categorize_article(title, text))

            email_body = (
                f"Source: {url}\n"
                f"Published: {date_str}\n"
                f"Category: {entry.get('category','?')} (via {entry.get('category_source','rules')})\n\n"
                f"{text}"
            )
            send_email(f"[Anthropic News] [{entry.get('category','?')}] {title}", email_body)

            seen_data[url] = entry
            new_count += 1
            save_seen_data(seen_data)

    # Backfill: any article missing a category gets one (cheap, runs once).
    backfilled = 0
    for url, info in seen_data.items():
        if "category" not in info:
            info.update(categorize_article(info.get("title", ""), info.get("text", "")))
            backfilled += 1
    if backfilled:
        print(f"Backfilled categories on {backfilled} article(s).")
        save_seen_data(seen_data)

    print(f"Found {new_count} new article(s).")
    update_index_html(seen_data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
