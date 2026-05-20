"""Categorize Anthropic news articles.

Strategy: rule-based pass (fast, free, deterministic) + optional GitHub Models
verification (catches misses, runs only when GITHUB_TOKEN is set).

Run standalone to re-categorize every article in seen_data.json:
    python categorize.py
    python categorize.py --no-verify   # rules only, skip GitHub Models

Or import:
    from categorize import categorize_article
    cat, conf, entities = categorize_article(title, text)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Iterable

import requests

SEEN_FILE = Path(__file__).parent / "seen_data.json"

# Order matters: first match wins, so put the more specific rules first.
# Each rule: (category, list of regex patterns checked against title + first
# ~1000 chars of body, case-insensitive).
CATEGORY_RULES: list[tuple[str, list[str]]] = [
    # Policy/safety announcements use unmistakable phrases — match first.
    ("Policy & Safety", [
        r"\bresponsible\s+scaling\s+policy\b",
        r"\belection\s+(safeguards?|integrity)\b",
        r"\b(usage|acceptable\s+use)\s+policy\b",
        r"\bmou\b.*\bsafety\b",
        r"\bai\s+safety\s+(commitment|policy|research)\b",
    ]),
    # Hiring/leadership announcements use very specific verbs — match before
    # Government & Region's location keywords scoop them.
    ("Org & Leadership", [
        r"\bappoints?\b.*\b(board|director|officer|vp|chief)",
        r"\bnames?\b.*\b(general\s+manager|gm|ceo|cto|cfo|president|head)\b",
        r"\blong-?term\s+benefit\s+trust\b",
        r"\bjoins?\s+anthropic\b",
    ]),
    # "Agents for X" is a product line, catch before Model Release.
    ("Product Launch", [
        r"\bagents?\s+for\s+(financial|legal|healthcare|enterprise|education)",
        r"\bintroducing\s+claude\s+(for|design|code)\b",
        r"\bclaude\s+for\s+(creative|small\s+business|enterprise|education|work|financial)",
        r"\banthropic\s+labs\b",
        r"\bnew\s+(product|feature|tool|capability)\b",
    ]),
    ("Model Release", [
        r"\b(introducing|announcing)\s+claude\s+(opus|sonnet|haiku)\b",
        r"\bclaude\s+(opus|sonnet|haiku)\s+\d",
        r"\b(new|latest|next)\s+(claude\s+)?(model|llm)\b",
        r"\bgenerally available\b.*\bclaude\b",
    ]),
    ("Infrastructure & Compute", [
        r"\bgigawatts?\b",
        r"\b(compute|capacity|data\s*centers?)\s+(deal|partnership|expansion)\b",
        r"\b(aws|amazon|google|broadcom|nvidia|spacex)\b.*\b(compute|gigawatt|capacity|chips?)\b",
        r"\bhigher\s+usage\s+limits?\b",
    ]),
    ("Policy & Safety", [
        r"\bresponsible\s+scaling\s+policy\b",
        r"\belection\s+(safeguards?|integrity)\b",
        r"\bai\s+safety\b",
        r"\b(usage|acceptable\s+use)\s+policy\b",
        r"\bmou\b.*\bsafety\b",
    ]),
    ("Government & Region", [
        r"\b(government|ministry|federal|sovereign)\b.*\b(partnership|mou|collaborat)",
        r"\bmou\b",
        r"\b(australia|japan|uk|eu|singapore|india|korea|germany|france)\b.*\b(partner|government|workforce|expand)",
        r"\bgeneral manager of\b",
    ]),
    ("Acquisition", [
        r"\banthropic\s+acquires?\b",
        r"\bacqui(sition|red|res)\b",
    ]),
    ("Investment & Funding", [
        r"\binvests?\s+\$\d",
        r"\b\$\d+\s*(million|billion|m|b)\s+(partnership|investment|commit|fund)",
        r"\bgates\s+foundation\b",
        r"\b(series\s+[a-f]|funding\s+round)\b",
    ]),
    ("Enterprise Deployment", [
        r"\b(kpmg|pwc|deloitte|ey|accenture|mckinsey|bain|bcg)\b",
        r"\bdeploy(ing|s|ed)?\s+claude\b",
        r"\bintegrat(es?|ing|ed)\s+claude\s+across\b",
        r"\bworkforce\s+of\b",
    ]),
    ("Partner Network & Ecosystem", [
        r"\bpartner\s+network\b",
        r"\b(broadcom|hellman|blackstone)\b.*\b(partner|collaborat|build)",
        r"\bclaude\s+partner\b",
    ]),
    ("Research & Institute", [
        r"\banthropic\s+institute\b",
        r"\b(research|paper|study)\s+(on|into|about)\b",
        r"\binterpretability\b",
        r"\balignment\s+research\b",
    ]),
    ("Brand & Vision", [
        r"\bclaude\s+is\s+a\s+space\b",
        r"\bour\s+(mission|vision|approach)\b",
    ]),
]

# A flat list of canonical categories (in case verifier wants to validate).
CATEGORIES: list[str] = [c for c, _ in CATEGORY_RULES] + ["Uncategorized"]

# Entity patterns — cheap named-entity-ish extraction. Keeps a list of known
# orgs/people/products and surfaces which appear in the article. Extending this
# list is the main way to improve entity coverage.
ENTITY_PATTERNS: list[tuple[str, str]] = [
    # type, regex
    ("company", r"\b(Amazon|AWS|Google|Broadcom|NVIDIA|SpaceX|Microsoft|Meta|OpenAI|Stainless|Blackstone|Hellman\s*&\s*Friedman|KPMG|PwC|Deloitte|EY|Accenture|McKinsey|NEC|Salesforce|Snowflake|Databricks|Palantir)\b"),
    ("foundation", r"\b(Gates Foundation|Anthropic Institute|Long-?Term Benefit Trust)\b"),
    ("region", r"\b(Australia|New Zealand|Japan|United Kingdom|UK|European Union|EU|Singapore|India|Korea|Germany|France|United States|USA)\b"),
    ("product", r"\bClaude\s+(Opus|Sonnet|Haiku|Code|Design|for\s+(?:Creative\s+Work|Small\s+Business|Enterprise|Education|Financial\s+Services))\s*[\d.]*\b"),
]


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def extract_entities(title: str, body: str) -> list[dict]:
    """Return a deduped list of {type, name} dicts found in the article."""
    haystack = f"{title}\n{body[:2000]}"
    found: dict[tuple[str, str], dict] = {}
    for ent_type, pattern in ENTITY_PATTERNS:
        for match in re.finditer(pattern, haystack, re.IGNORECASE):
            name = _normalize(match.group(0))
            # Title-case standardization for display
            key = (ent_type, name.lower())
            if key not in found:
                found[key] = {"type": ent_type, "name": name}
    return list(found.values())


def categorize_by_rules(title: str, body: str) -> tuple[str, float]:
    """Return (category, confidence).

    Two-pass matching:
      1) Strong pass: match patterns against the TITLE only. Confidence 1.0.
         This prevents body mentions of past products (e.g. "built on Opus 4")
         from miscategorizing partnership/enterprise articles.
      2) Weak pass: match against title + first 1500 chars of body.
         Confidence 0.6 — verifier will likely re-examine.
    """
    title_only = title
    full = f"{title}\n{body[:1500]}"

    # Strong pass: title only
    for category, patterns in CATEGORY_RULES:
        for pat in patterns:
            if re.search(pat, title_only, re.IGNORECASE):
                return category, 1.0

    # Weak pass: include body
    for category, patterns in CATEGORY_RULES:
        for pat in patterns:
            if re.search(pat, full, re.IGNORECASE):
                return category, 0.6

    return "Uncategorized", 0.0


# ---------------------------------------------------------------------------
# GitHub Models verifier
# ---------------------------------------------------------------------------

GH_MODELS_ENDPOINT = "https://models.github.ai/inference/chat/completions"
# Pick a small, fast model — categorization is easy.
GH_MODEL = os.environ.get("GH_MODEL", "openai/gpt-4o-mini")


def _verifier_prompt(title: str, body: str, rule_category: str) -> list[dict]:
    cat_list = ", ".join(c for c in CATEGORIES if c != "Uncategorized")
    system = (
        "You categorize Anthropic news articles into exactly one category. "
        f"Allowed categories: {cat_list}. "
        "Respond with ONLY a compact JSON object: "
        '{"category": "<one of the allowed>", "agree": true|false, "reason": "<short>"}'
    )
    user = (
        f"Title: {title}\n\n"
        f"Body (first 1500 chars):\n{body[:1500]}\n\n"
        f"Rule-based system guessed: {rule_category}\n"
        "Pick the best category. If the rule guess is correct, set agree=true and "
        "category to the same value. If wrong, set agree=false and category to your pick."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def verify_with_github_models(title: str, body: str, rule_category: str) -> dict | None:
    """Call GitHub Models to double-check. Returns dict or None on any failure."""
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        return None
    try:
        resp = requests.post(
            GH_MODELS_ENDPOINT,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json={
                "model": GH_MODEL,
                "messages": _verifier_prompt(title, body, rule_category),
                "temperature": 0,
                "max_tokens": 200,
            },
            timeout=30,
        )
        if resp.status_code != 200:
            print(f"  [verifier] HTTP {resp.status_code}: {resp.text[:200]}")
            return None
        content = resp.json()["choices"][0]["message"]["content"]
        # Strip code fences if the model added any
        content = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.MULTILINE).strip()
        data = json.loads(content)
        if data.get("category") not in CATEGORIES:
            return None
        return data
    except Exception as e:
        print(f"  [verifier] failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def categorize_article(
    title: str,
    body: str,
    *,
    use_verifier: bool = True,
) -> dict:
    """Categorize one article. Returns a dict ready to merge into seen_data.json."""
    rule_cat, rule_conf = categorize_by_rules(title, body)
    entities = extract_entities(title, body)

    result = {
        "category": rule_cat,
        "category_source": "rules",
        "category_confidence": rule_conf,
        "entities": entities,
    }

    if not use_verifier:
        return result

    verdict = verify_with_github_models(title, body, rule_cat)
    if verdict is None:
        # Verifier unavailable / failed — keep rules result
        return result

    if verdict.get("agree"):
        result["category_source"] = "rules+verified"
        result["category_confidence"] = 1.0
    else:
        # Verifier disagreed. Trust it but record the disagreement.
        result["category"] = verdict["category"]
        result["category_source"] = "verifier_override"
        result["category_confidence"] = 0.75
        result["category_rule_guess"] = rule_cat
        result["category_verifier_reason"] = verdict.get("reason", "")
    return result


# ---------------------------------------------------------------------------
# CLI: re-categorize everything in seen_data.json
# ---------------------------------------------------------------------------

def recategorize_all(use_verifier: bool = True) -> None:
    if not SEEN_FILE.exists():
        print(f"No {SEEN_FILE}; nothing to do.")
        return
    data = json.loads(SEEN_FILE.read_text())
    print(f"Re-categorizing {len(data)} articles (verifier={'on' if use_verifier else 'off'})")

    from collections import Counter
    counts: Counter = Counter()
    overrides = 0

    for url, info in data.items():
        result = categorize_article(
            info.get("title", ""),
            info.get("text", ""),
            use_verifier=use_verifier,
        )
        info.update(result)
        counts[result["category"]] += 1
        if result["category_source"] == "verifier_override":
            overrides += 1
            print(f"  override: {info.get('title','')[:60]!r}")
            print(f"           rules said {result['category_rule_guess']}, "
                  f"verifier said {result['category']}: {result.get('category_verifier_reason','')}")

    SEEN_FILE.write_text(json.dumps(data, indent=2) + "\n")
    print("\nCategory distribution:")
    for cat, n in counts.most_common():
        print(f"  {n:>3}  {cat}")
    if use_verifier:
        print(f"\nVerifier overrode rules on {overrides} article(s).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-verify", action="store_true",
                        help="Skip GitHub Models, rules only")
    args = parser.parse_args()
    recategorize_all(use_verifier=not args.no_verify)
