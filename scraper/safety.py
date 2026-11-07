"""Content exclusions: adult, illegal trade, weapons sales, hate speech.

Patterns live in scraper/blocked_patterns.txt (one regex per line, comments
allowed) so the blocklist is editable without touching code.
"""
from __future__ import annotations

import re
from pathlib import Path

PATTERNS_FILE = Path(__file__).with_name("blocked_patterns.txt")


def _compile() -> re.Pattern:
    lines = [
        line.strip()
        for line in PATTERNS_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    return re.compile("|".join(lines), re.IGNORECASE)


_REGEX = _compile()


def is_safe(text: str) -> bool:
    return not _REGEX.search(text or "")
