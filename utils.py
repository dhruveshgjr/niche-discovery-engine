"""Small shared helpers: UTC timestamps, retries with backoff, tolerant JSON parsing."""
from __future__ import annotations

import json
import random
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC)


def clamp(value: float, lo: float = 0.0, hi: float = 10.0) -> float:
    return max(lo, min(hi, value))


def retry_call[T](
    fn: Callable[[], T],
    *,
    attempts: int = 3,
    base_delay: float = 1.0,
    retry_on: tuple[type[Exception], ...] = (Exception,),
    logger=None,
) -> T:
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except retry_on as exc:
            last_exc = exc
            if attempt == attempts:
                break
            delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
            if logger:
                logger.warning(
                    "attempt %d/%d failed (%s); retrying in %.1fs",
                    attempt, attempts, exc, delay,
                )
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc


def truncate_text(text: str, max_chars: int) -> str:
    """Keep head and tail so context overflow loses the middle, not the instructions."""
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return text[:half] + "\n...[truncated]...\n" + text[-half:]


def parse_json_loose(text: str):
    """Parse model output that may be fenced or wrapped in prose."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text.strip())
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    for match in re.finditer(r"[\{\[]", text):
        try:
            obj, _ = decoder.raw_decode(text, match.start())
            return obj
        except json.JSONDecodeError:
            continue
    raise ValueError("No valid JSON found in model output")


def strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text or "").strip()


def parse_date(value) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        pass
    try:  # X/Twitter timestamp format: "Mon Nov 02 00:00:00 +0000 2026"
        return datetime.strptime(str(value), "%a %b %d %H:%M:%S %z %Y")
    except ValueError:
        return None