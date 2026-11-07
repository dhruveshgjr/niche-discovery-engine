"""Deterministic, offline stand-in for the LLM.

Used in demo mode (LM Studio unreachable) and as a last-resort fallback when a
live model call fails, so the pipeline always completes end-to-end.
"""
from __future__ import annotations

import re

PAIN_SIGNALS = [
    "no api", "missing api", "manual", "copy", "paste", "spreadsheet", "excel",
    "csv", "hours", "tedious", "frustrat", "bottleneck", "wish", "nightmare",
    "data entry", "scrape", "scraping", "extract", "export", "integration",
    "zapier", "repetitive", "every morning", "every week", "by hand",
]

QUERY_MODIFIERS = [
    "{s} tool",
    "no api {s}",
    "{s} manual workaround",
    "frustrated with {s}",
    "{s} alternative",
]


def generate_queries(seeds: list[str], count: int = 20) -> list[str]:
    queries: list[str] = []
    for seed in seeds:
        for modifier in QUERY_MODIFIERS:
            q = modifier.format(s=seed)
            if q not in queries:
                queries.append(q)
            if len(queries) >= count:
                return queries
    return queries[:count]


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]


def _persona(text: str) -> str:
    for keyword, persona in [
        ("agency", "Agency owner"),
        ("recruit", "Recruiter"),
        ("developer", "Developer"),
        ("engineer", "Engineer"),
        ("founder", "Startup founder"),
        ("small business", "Small business owner"),
        ("marketer", "Marketer"),
        ("saas", "SaaS founder"),
        ("ops", "Operations manager"),
    ]:
        if keyword in text:
            return persona
    return "Business operator"


def _current_solution(text: str) -> str:
    if any(w in text for w in ("spreadsheet", "excel", "csv")):
        return "Manual spreadsheet work"
    if "copy" in text or "paste" in text:
        return "Manual copy-paste"
    if "zapier" in text:
        return "Zapier / no-code automation"
    if "assistant" in text:
        return "Human assistant / VA"
    return "Manual process"


def extract_pain_points(post: dict) -> list[dict]:
    text = f"{post.get('title', '')}. {post.get('body', '')}"
    lower = text.lower()
    hits = [s for s in _sentences(text) if any(sig in s.lower() for sig in PAIN_SIGNALS)]
    if not hits:
        return []
    best = max(hits, key=lambda s: sum(sig in s.lower() for sig in PAIN_SIGNALS))
    buyer = 3.0
    if any(w in lower for w in ("pay", "budget", "buy", "hire", "$")):
        buyer += 2.0
    if any(w in lower for w in ("tool", "software", "saas")):
        buyer += 1.0
    severity = 4.0
    if any(w in lower for w in ("hours", "nightmare", "daily", "every day")):
        severity += 2.0
    if any(w in lower for w in ("every week", "every morning", "constantly")):
        severity += 1.0
    return [{
        "pain_statement": best[:300],
        "persona": _persona(lower),
        "buyer_intent": min(buyer, 10.0),
        "severity": min(severity, 10.0),
        "current_solution": _current_solution(lower),
        "extraction_fit": any(
            w in lower for w in ("api", "scrape", "scraping", "extract", "export", "csv", "data")
        ),
    }]


CLUSTER_TOPICS = [
    ("Missing APIs & Locked-Down Data", ["api", "integration", "export", "zapier"]),
    ("Manual Data Entry & Spreadsheet Hell", ["copy", "paste", "spreadsheet", "excel", "csv", "data entry"]),
    ("Web Data Extraction & Monitoring", ["scrape", "scraping", "extract", "extraction", "monitor"]),
    ("Social & Ad-Platform Reporting", ["tweet", "twitter", "facebook", "ads", "analytics", "reporting"]),
    ("Hiring & Recruiting Operations", ["hiring", "recruit", "job", "candidate"]),
]


def cluster_pains(pains: list[dict]) -> list[dict]:
    groups: dict[str, dict] = {}
    for pain in pains:
        text = pain["pain_statement"].lower()
        for name, keywords in CLUSTER_TOPICS:
            if any(kw in text for kw in keywords):
                group = groups.setdefault(name, {"cluster_name": name, "pain_ids": []})
                group["pain_ids"].append(pain["id"])
                break
        else:
            group = groups.setdefault(
                "General Workflow Automation",
                {"cluster_name": "General Workflow Automation", "pain_ids": []},
            )
            group["pain_ids"].append(pain["id"])
    return list(groups.values())


PRODUCT_IDEAS = {
    "Missing APIs & Locked-Down Data": (
        "A session-cookie extraction API that turns cookie-gated dashboards and "
        "API-less SaaS tools into clean, documented JSON endpoints for {persona}s, "
        "with scheduled syncs into their existing stack."
    ),
    "Manual Data Entry & Spreadsheet Hell": (
        "An automation layer for {persona}s that watches inboxes, PDFs, and web "
        "pages, extracts structured rows, and writes them straight into "
        "Excel/Sheets/CRM — eliminating the copy-paste loop."
    ),
    "Web Data Extraction & Monitoring": (
        "A lightweight extraction-and-monitoring service for {persona}s: "
        "point it at a page, get structured data plus change alerts, no "
        "headless-browser infrastructure to babysit."
    ),
    "Social & Ad-Platform Reporting": (
        "A cross-account reporting tool for {persona}s that pulls X/Facebook/ad "
        "metrics on a schedule and delivers one client-ready report instead of "
        "manual screenshots and spreadsheet merges."
    ),
    "Hiring & Recruiting Operations": (
        "A pipeline tool for {persona}s that aggregates job-board and community "
        "hiring signals, dedupes candidates/roles, and syncs them into an ATS."
    ),
}

DEFAULT_IDEA = (
    "A focused micro-SaaS for {persona}s that automates the recurring manual "
    "workflow described by this cluster, starting with one narrow integration."
)


def strategize(cluster_name: str, pains: list[dict]) -> dict:
    personas = sorted({p.get("persona") for p in pains if p.get("persona")})
    persona = personas[0] if personas else "operators"
    idea = PRODUCT_IDEAS.get(cluster_name, DEFAULT_IDEA).format(persona=persona)
    plan = (
        f"1) Interview 5 {persona}s recruited from the source threads this week.\n"
        "2) Put up a landing page describing the product; drive 100 visits from the "
        "same communities and measure email conversion (>15% = signal).\n"
        "3) Run a concierge MVP: deliver the outcome manually for 3 design partners "
        "at $49-99/mo before writing software.\n"
        "4) Only build once 2 of 3 partners renew; pre-sell annual plans to fund development."
    )
    return {
        "product_idea": idea,
        "validation_plan": plan,
        "competition": 5.0,
        "legal_risk": 3.0,
        "reachability": 7.0,
    }