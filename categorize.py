"""Categorize Anthropic news articles.

Strategy: rule-based pass (fast, free, deterministic) + GitHub Models
verification. Unlike the previous version, verifier failures are no longer
swallowed — they are recorded, counted, and can abort a bulk re-categorization
so that a broken token cannot quietly downgrade months of verified labels.

Run standalone to re-categorize every article in seen_data.json:
    python categorize.py
    python categorize.py --no-verify         # rules only, skip GitHub Models
    python categorize.py --allow-degraded    # write even if the verifier died

Or import:
    from categorize import categorize_article, verifier_failures
    result = categorize_article(title, text)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

SEEN_FILE = Path(__file__).parent / "seen_data.json"
RULES_VERSION = 2

# ---------------------------------------------------------------------------
# Category rules
# ---------------------------------------------------------------------------
# Order matters: first match wins, so put the more specific rules first.
# Each rule: (category, list of regex patterns checked against the title, then
# against title + body on a weaker second pass).

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
    # Cyber/safeguards content became a distinct genre in mid-2026 and had no
    # home — it was the single largest source of "Uncategorized".
    ("Cybersecurity & Safeguards", [
        r"\bcyber(security|\s+threats?|\s+safeguards?|\s+capabilit)",
        r"\bsafeguards?\b",
        r"\bjailbreak\w*\b",
        r"\bwatermark(ing)?\b",
        r"\b(red[-\s]team\w*|penetration\s+test\w*|vulnerability\s+research)\b",
    ]),
    # Transparency/position pieces — reports, retrospectives, stated positions.
    ("Transparency & Reports", [
        r"\bpublic\s+record\b",
        r"\bour\s+position\s+on\b",
        r"\binvestigating\b.*\bincidents?\b",
        r"\bwhat\s+we\s+learned\b",
        r"\btransparency\s+(report|hub)\b",
    ]),
    # "Agents for X" is a product line, catch before Model Release.
    ("Product Launch", [
        r"\bagents?\s+for\s+(financial|legal|healthcare|enterprise|education)",
        r"\bintroducing\s+claude\s+(for|design|code|tag|corps|science|cowork)\b",
        r"\bclaude\s+for\s+(creative|small\s+business|enterprise|education|work|financial|teachers?)",
        r"\banthropic\s+labs\b",
        r"\bnew\s+(product|feature|tool|capability)\b",
    ]),
    ("Model Release", [
        r"\b(introducing|announcing)\s+claude\s+(opus|sonnet|haiku|fable|mythos)\b",
        r"\bclaude\s+(opus|sonnet|haiku|fable|mythos)\s+\d",
        r"\b(new|latest|next)\s+(claude\s+)?(model|llm)\b",
        r"\bgenerally\s+available\b.*\bclaude\b",
    ]),
    ("Infrastructure & Compute", [
        r"\bgigawatts?\b",
        r"\b(compute|capacity|data\s*centers?)\s+(deal|partnership|expansion)\b",
        r"\b(aws|amazon|google|broadcom|nvidia|spacex)\b.*\b(compute|gigawatt|capacity|chips?)\b",
        r"\bhigher\s+usage\s+limits?\b",
    ]),
    # Moved above Enterprise Deployment / Partner Network so it can actually
    # match. In the old ordering this category was unreachable — 0 of 62
    # articles ever landed here. Note: "general manager of" was removed; it
    # belongs to Org & Leadership and was the main source of the collision.
    ("Government & Region", [
        r"\b(government|ministry|federal|sovereign)\b.*\b(partnership|mou|collaborat)",
        r"\bmou\b",
        r"\bopens?\s+(a\s+|new\s+)?\w+\s+office\b",
        r"\b(australia|japan|uk|eu|singapore|india|korea|germany|france|italy|canada)\b"
        r".*\b(partner|government|workforce|expand|office)",
    ]),
    ("Acquisition", [
        r"\banthropic\s+acquires?\b",
        r"\bacqui(sition|red|res)\b",
    ]),
    ("Investment & Funding", [
        r"\binvests?\s+\$\d",
        r"\b\$\d+\s*(million|billion|m|b)\s+(partnership|investment|commit|fund)",
        r"\bgates\s+foundation\b",
        r"\b(series\s+[a-h]|funding\s+round)\b",
        r"\bs-1\b|\bipo\b",
    ]),
    ("Enterprise Deployment", [
        r"\b(kpmg|pwc|deloitte|ey|accenture|mckinsey|bain|bcg)\b",
        r"\bdeploy(ing|s|ed)?\s+claude\b",
        r"\bintegrat(es?|ing|ed)\s+claude\s+across\b",
        r"\bworkforce\s+of\b",
    ]),
    ("Partner Network & Ecosystem", [
        r"\bpartner\s+network\b",
        r"\b(broadcom|hellman|blackstone|dxc|tcs|cognizant|ust|nec)\b.*\b(partner|collaborat|build|integrat)",
        r"\bclaude\s+partner\b",
        r"\bproject\s+glasswing\b",
    ]),
    ("Research & Institute", [
        r"\banthropic\s+institute\b",
        r"\b(research|paper|study)\s+(on|into|about|agenda)\b",
        r"\binterpretability\b",
        r"\balignment\s+research\b",
    ]),
    ("Brand & Vision", [
        r"\bclaude\s+is\s+a\s+space\b",
        r"\bour\s+(mission|vision|approach)\b",
    ]),
]

# Deduped, order-preserving. The old version built this with a list
# comprehension over CATEGORY_RULES, which contained "Policy & Safety" twice —
# so the verifier prompt listed the same category twice.
CATEGORIES: list[str] = list(dict.fromkeys(c for c, _ in CATEGORY_RULES))

# What the verifier is allowed to return. "Uncategorized" is deliberately
# excluded: the prompt forbids it, so accepting it would record a confident
# 0.75 "override" onto a non-answer.
VERIFIER_CATEGORIES: list[str] = CATEGORIES

# Exposed for callers that want the full label space including the fallback.
ALL_CATEGORIES: list[str] = CATEGORIES + ["Uncategorized"]


# ---------------------------------------------------------------------------
# Entity extraction
# ---------------------------------------------------------------------------
# Product names must be maintained by hand. The previous list predated the
# Fable/Mythos generation entirely: "Fable" appears 189 times across the corpus
# and "Mythos" 110, and neither ever produced a single entity chip.

ENTITY_PATTERNS: list[tuple[str, str]] = [
    ("company", r"\b(Amazon|AWS|Google|Broadcom|NVIDIA|SpaceX|Microsoft|Meta|OpenAI"
                r"|Stainless|Blackstone|Hellman\s*&\s*Friedman|KPMG|PwC|Deloitte|EY"
                r"|Accenture|McKinsey|NEC|DXC|TCS|Cognizant|UST|Salesforce|Snowflake"
                r"|Databricks|Palantir)\b"),
    ("foundation", r"\b(Gates Foundation|Anthropic Institute|Long-?Term Benefit Trust"
                   r"|Public First Action)\b"),
    ("region", r"\b(Australia|New Zealand|Japan|United Kingdom|UK|European Union|EU"
               r"|Singapore|India|Korea|Germany|France|Italy|Canada|United States|USA)\b"),
    ("product", r"\bClaude\s+(?:Opus|Sonnet|Haiku|Fable|Mythos|Code|Design|Tag|Corps"
                r"|Science|Cowork|Chrome|Excel|PowerPoint"
                r"|for\s+(?:Creative\s+Work|Small\s+Business|Enterprise|Education"
                r"|Teachers|Financial\s+Services))"
                r"(?:\s+(?:Preview|\d+(?:\.\d+)?))?\b"),
    ("program", r"\b(Project Glasswing|Cyber Verification|Anthropic Public Record"
                r"|Anthropic Economic Index)\b"),
]

# How much of the article body to scan for entities. The old value was 2000
# characters *including* the scraped nav/date preamble, which is why
# "Claude Code" topped the entity chart at 17 — that is exactly the number of
# articles mentioning it anywhere in that window, boilerplate included.
ENTITY_SCAN_CHARS = 4000

# How much of the body the rules and the verifier see.
RULE_SCAN_CHARS = 1500


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _strip_preamble(title: str, body: str) -> str:
    """Drop the scraped 'Product / Announcements / Title / Date' header.

    monitor.py captures the article's breadcrumb and date above the body. Those
    lines are near-identical across articles and inflate both rule matches and
    entity counts. If the title is found near the top, start after it.
    """
    if not title:
        return body
    idx = body.find(title)
    if 0 <= idx < 500:
        return body[idx + len(title):].lstrip()
    return body


def extract_entities(title: str, body: str) -> list[dict]:
    """Return a deduped list of {type, name} dicts found in the article.

    Note: this records *presence per article*, not mention count. The trends
    panel in monitor.py labels the result "most-mentioned", which is not what
    this measures — rename the heading or switch to counting matches.
    """
    clean = _strip_preamble(title, body)
    haystack = f"{title}\n{clean[:ENTITY_SCAN_CHARS]}"
    found: dict[tuple[str, str], dict] = {}
    for ent_type, pattern in ENTITY_PATTERNS:
        for match in re.finditer(pattern, haystack, re.IGNORECASE):
            name = _normalize(match.group(0))
            key = (ent_type, name.lower())
            if key not in found:
                found[key] = {"type": ent_type, "name": name}
    return list(found.values())


def categorize_by_rules(title: str, body: str) -> tuple[str, float]:
    """Return (category, confidence).

    Two-pass matching:
      1) Strong pass: match patterns against the TITLE only. Confidence 1.0.
      2) Weak pass: match against title + first RULE_SCAN_CHARS of body.
         Confidence 0.6 — verifier will likely re-examine.
    """
    clean = _strip_preamble(title, body)
    full = f"{title}\n{clean[:RULE_SCAN_CHARS]}"

    for category, patterns in CATEGORY_RULES:
        for pat in patterns:
            if re.search(pat, title, re.IGNORECASE):
                return category, 1.0

    for category, patterns in CATEGORY_RULES:
        for pat in patterns:
            if re.search(pat, full, re.IGNORECASE):
                return category, 0.6

    return "Uncategorized", 0.0


# ---------------------------------------------------------------------------
# GitHub Models verifier
# ---------------------------------------------------------------------------

# Provider-agnostic: any OpenAI-compatible /chat/completions endpoint works.
# Defaults to Gemini via Google's OpenAI compatibility layer. GitHub Models was
# hardcoded here and its retirement took the verifier down with it; keeping all
# three values in env vars means the next migration is a secrets change.
#
# Verify the current model name and free-tier limits at ai.google.dev before
# relying on the default below.
VERIFIER_ENDPOINT = os.environ.get(
    "VERIFIER_ENDPOINT",
    "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
)
VERIFIER_MODEL = os.environ.get("VERIFIER_MODEL", "gemini-2.0-flash")
# Which env var holds the key. Lets you switch provider without a code change.
VERIFIER_KEY_ENV = os.environ.get("VERIFIER_KEY_ENV", "GEMINI_API_KEY")

MAX_ATTEMPTS = 3
RETRY_STATUSES = {408, 429, 500, 502, 503, 504}
# Statuses that will not improve on retry, and will not improve on the next
# article either: bad credentials, missing scope, or an endpoint that is gone.
# 410 is what GitHub Models returns during its retirement brownouts.
TERMINAL_STATUSES = {401, 403, 404, 410}

# Module-level failure log. Callers check this to decide whether a run is
# trustworthy; the old code had no way to distinguish "verifier off" from
# "verifier broken".
_VERIFIER_FAILURES: list[str] = []

# Set once a TERMINAL_STATUS is seen. Without this, a retired endpoint gets
# hit once per article: 62 doomed round trips to learn what the first one said.
_VERIFIER_DISABLED: str | None = None


class VerifierUnavailable(Exception):
    """The verifier could not be reached or returned something unusable."""


def verifier_failures() -> list[str]:
    """Failure messages recorded since process start (or last reset)."""
    return list(_VERIFIER_FAILURES)


def reset_verifier_failures() -> None:
    global _VERIFIER_DISABLED
    _VERIFIER_FAILURES.clear()
    _VERIFIER_DISABLED = None


def verifier_disabled() -> str | None:
    """Reason the verifier was shut off for this process, if it was."""
    return _VERIFIER_DISABLED


def _extract_json_object(text: str) -> dict:
    """Pull the first balanced {...} out of a model response.

    The old implementation used
        re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE)
    which strips backticks at *any* line boundary, including inside a string
    value. Scanning for a balanced brace span is both simpler and safer.
    """
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for i, ch in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                return json.loads(text[start:i + 1])
    raise ValueError("no JSON object found in verifier response")


def _verifier_prompt(title: str, body: str, rule_category: str) -> list[dict]:
    cat_list = ", ".join(VERIFIER_CATEGORIES)
    system = (
        "You categorize Anthropic news articles into exactly one category. "
        f"Allowed categories: {cat_list}. "
        "Respond with ONLY a compact JSON object: "
        '{"category": "<one of the allowed>", "agree": true|false, "reason": "<short>"}'
    )
    user = (
        f"Title: {title}\n\n"
        f"Body (first {RULE_SCAN_CHARS} chars):\n{body[:RULE_SCAN_CHARS]}\n\n"
        f"Rule-based system guessed: {rule_category}\n"
        "Pick the best category. If the rule guess is correct, set agree=true and "
        "category to the same value. If wrong, set agree=false and category to your pick."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def verify_with_llm(title: str, body: str, rule_category: str) -> dict:
    """Ask the configured LLM to double-check the rule guess.

    Raises VerifierUnavailable on any failure. It never returns None — the old
    signature made "no token", "HTTP 401", "bad JSON" and "connection refused"
    indistinguishable from a healthy skip, which is how the verifier stayed
    dead for weeks without anyone noticing.
    """
    global _VERIFIER_DISABLED

    if _VERIFIER_DISABLED:
        raise VerifierUnavailable(_VERIFIER_DISABLED)

    token = os.environ.get(VERIFIER_KEY_ENV)
    if not token:
        _VERIFIER_DISABLED = f"{VERIFIER_KEY_ENV} is not set"
        raise VerifierUnavailable(_VERIFIER_DISABLED)

    last_error = "unknown"
    for attempt in range(MAX_ATTEMPTS):
        try:
            resp = requests.post(
                VERIFIER_ENDPOINT,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                json={
                    "model": VERIFIER_MODEL,
                    "messages": _verifier_prompt(title, body, rule_category),
                    "temperature": 0,
                    "max_tokens": 200,
                },
                timeout=30,
            )
        except requests.RequestException as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            time.sleep(2 ** attempt)
            continue

        if resp.status_code in RETRY_STATUSES:
            last_error = f"HTTP {resp.status_code}"
            time.sleep(2 ** attempt)
            continue

        if resp.status_code in TERMINAL_STATUSES:
            _VERIFIER_DISABLED = f"HTTP {resp.status_code}: {resp.text[:200]}"
            raise VerifierUnavailable(_VERIFIER_DISABLED)

        if resp.status_code != 200:
            raise VerifierUnavailable(
                f"HTTP {resp.status_code}: {resp.text[:200]}"
            )

        try:
            content = resp.json()["choices"][0]["message"]["content"]
            data = _extract_json_object(content)
        except (KeyError, IndexError, TypeError, ValueError,
                json.JSONDecodeError) as exc:
            raise VerifierUnavailable(f"unparseable response: {exc}") from exc

        if data.get("category") not in VERIFIER_CATEGORIES:
            raise VerifierUnavailable(
                f"verifier returned unknown category {data.get('category')!r}"
            )
        return data

    raise VerifierUnavailable(f"{MAX_ATTEMPTS} attempts failed, last: {last_error}")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

# Keys written only when the verifier disagreed. They must be cleared before a
# re-categorization writes a new result, or an entry can end up claiming a
# disagreement that did not happen in this run.
OPTIONAL_KEYS = ("category_rule_guess", "category_verifier_reason", "verifier_error", "rules_version")


def categorize_article(
    title: str,
    body: str,
    *,
    use_verifier: bool = True,
    strict: bool = False,
) -> dict:
    """Categorize one article. Returns a dict ready to merge into seen_data.json.

    If the verifier is unavailable: with strict=False (default) the rules
    result is returned with a "verifier_error" field and the failure is
    recorded in verifier_failures(); with strict=True the exception propagates.
    """
    rule_cat, rule_conf = categorize_by_rules(title, body)
    entities = extract_entities(title, body)

    result = {
        "category": rule_cat,
        "category_source": "rules",
        "category_confidence": rule_conf,
        "entities": entities,
    }

    if not use_verifier:
        result["rules_version"] = RULES_VERSION
        return result

    try:
        verdict = verify_with_llm(title, body, rule_cat)
    except VerifierUnavailable as exc:
        _VERIFIER_FAILURES.append(f"{title[:60]!r}: {exc}")
        if strict:
            raise
        result["verifier_error"] = str(exc)
        return result

    if verdict.get("agree"):
        result["category_source"] = "rules+verified"
        # Agreement on a weak body match is not as strong as a title match.
        result["category_confidence"] = 1.0 if rule_conf >= 1.0 else 0.9
    else:
        result["category"] = verdict["category"]
        result["category_source"] = "verifier_override"
        result["category_confidence"] = 0.75
        result["category_rule_guess"] = rule_cat
        result["category_verifier_reason"] = verdict.get("reason", "")
    result["rules_version"] = RULES_VERSION
    return result


# ---------------------------------------------------------------------------
# CLI: re-categorize everything in seen_data.json
# ---------------------------------------------------------------------------

def recategorize_all(use_verifier: bool = True, allow_degraded: bool = False) -> int:
    """Re-label every article. Returns a process exit code.

    Nothing is written until every article has been processed. If the verifier
    died partway through, the run aborts by default rather than silently
    downgrading previously verified labels to bare rules output.
    """
    if not SEEN_FILE.exists():
        print(f"No {SEEN_FILE}; nothing to do.")
        return 0

    data = json.loads(SEEN_FILE.read_text())
    print(f"Re-categorizing {len(data)} articles (verifier={'on' if use_verifier else 'off'})")
    reset_verifier_failures()

    from collections import Counter
    counts: Counter = Counter()
    overrides = 0
    staged: dict[str, dict] = {}

    for url, info in data.items():
        result = categorize_article(
            info.get("title", ""),
            info.get("text", ""),
            use_verifier=use_verifier,
        )
        staged[url] = result
        counts[result["category"]] += 1
        if result["category_source"] == "verifier_override":
            overrides += 1
            print(f"  override: {info.get('title','')[:60]!r}")
            print(f"           rules said {result['category_rule_guess']}, "
                  f"verifier said {result['category']}: "
                  f"{result.get('category_verifier_reason','')}")

    failures = verifier_failures()
    if use_verifier and failures:
        print(f"\n!! verifier failed on {len(failures)}/{len(data)} articles:",
              file=sys.stderr)
        for line in failures[:5]:
            print(f"   {line}", file=sys.stderr)
        if len(failures) > 5:
            print(f"   ... and {len(failures) - 5} more", file=sys.stderr)
        if not allow_degraded:
            print("\nAborting without writing. Previously verified labels are "
                  "intact. Fix the token/endpoint, or re-run with "
                  "--allow-degraded to accept rules-only output.", file=sys.stderr)
            return 1

    for url, result in staged.items():
        entry = data[url]
        for key in OPTIONAL_KEYS:
            entry.pop(key, None)
        entry.update(result)

    SEEN_FILE.write_text(json.dumps(data, indent=2) + "\n")

    print("\nCategory distribution:")
    for cat, n in counts.most_common():
        print(f"  {n:>3}  {cat}")
    if use_verifier:
        print(f"\nVerifier overrode rules on {overrides} article(s).")
        print(f"Verifier failed on {len(failures)} article(s).")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-verify", action="store_true",
                        help="Skip GitHub Models, rules only")
    parser.add_argument("--allow-degraded", action="store_true",
                        help="Write results even if the verifier was unavailable")
    args = parser.parse_args()
    sys.exit(recategorize_all(
        use_verifier=not args.no_verify,
        allow_degraded=args.allow_degraded,
    ))
