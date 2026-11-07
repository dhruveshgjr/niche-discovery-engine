"""Local sample data for demo mode and offline pipeline testing."""
from __future__ import annotations

import json

from config import PROJECT_ROOT

FIXTURE_PATH = PROJECT_ROOT / "fixtures" / "raw_posts.json"


def load_fixture_posts(sources: list[str] | None = None) -> list[dict]:
    posts = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    if sources:
        filtered = [p for p in posts if p.get("source") in sources]
        if filtered:
            return filtered
    return posts