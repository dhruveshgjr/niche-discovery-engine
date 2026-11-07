"""Live data sources: HN Algolia, Reddit public JSON, job boards (RSS/API), YC RFS.

Every source is polite (delays, descriptive UA), defensive (per-request error
handling), and returns plain dicts shaped like RawPost fields.
"""
from __future__ import annotations

from datetime import UTC, datetime

import feedparser
import httpx
from bs4 import BeautifulSoup

from logging_setup import get_logger
from scraper.base import BaseSource
from utils import strip_html

logger = get_logger(__name__)


def _unix_to_iso(value) -> str | None:
    try:
        return datetime.fromtimestamp(float(value), tz=UTC).isoformat()
    except (TypeError, ValueError):
        return None


class HackerNewsSource(BaseSource):
    """Algolia HN search API — covers Ask HN, Show HN, and keyword queries."""

    name = "hacker_news"
    API = "https://hn.algolia.com/api/v1/search"

    def collect(self, queries: list[str], limit: int) -> list[dict]:
        posts: list[dict] = []
        seen: set[str] = set()
        for query in queries:
            if len(posts) >= limit:
                break
            try:
                resp = self._get(self.API, params={
                    "query": query,
                    "tags": "story",
                    "hitsPerPage": min(30, limit - len(posts)),
                })
                hits = resp.json().get("hits", [])
            except Exception as exc:
                logger.warning("HN query %r failed: %s", query, exc)
                continue
            for hit in hits:
                url = f"https://news.ycombinator.com/item?id={hit['objectID']}"
                if url in seen:
                    continue
                seen.add(url)
                title = strip_html(hit.get("title") or "")[:500]
                body = (strip_html(hit.get("story_text") or "") or title)[:4000]
                posts.append({
                    "source": self.name,
                    "url": url,
                    "title": title,
                    "body": body,
                    "date_posted": hit.get("created_at"),
                    "engagement": int(hit.get("points") or 0),
                })
        return posts[:limit]


class RedditSource(BaseSource):
    """Polite public JSON search across target subreddits. No auth required."""

    name = "reddit"
    SUBS = ["SaaS", "Entrepreneur", "smallbusiness"]

    def collect(self, queries: list[str], limit: int) -> list[dict]:
        posts: list[dict] = []
        seen: set[str] = set()
        for query in queries:
            for sub in self.SUBS:
                if len(posts) >= limit:
                    return posts
                try:
                    resp = self._get(
                        f"https://www.reddit.com/r/{sub}/search.json",
                        params={
                            "q": query, "restrict_sr": "1", "limit": 25,
                            "sort": "relevance", "t": "year",
                        },
                    )
                    children = resp.json().get("data", {}).get("children", [])
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code in (403, 429):
                        logger.warning("Reddit rate-limited/blocked (%s); stopping source",
                                       exc.response.status_code)
                        return posts
                    logger.warning("Reddit r/%s query %r failed: %s", sub, query, exc)
                    continue
                except Exception as exc:
                    logger.warning("Reddit r/%s query %r failed: %s", sub, query, exc)
                    continue
                for child in children:
                    data = child.get("data", {})
                    url = f"https://www.reddit.com{data.get('permalink', '')}"
                    if not data.get("permalink") or url in seen:
                        continue
                    seen.add(url)
                    posts.append({
                        "source": self.name,
                        "url": url,
                        "title": data.get("title", "")[:500],
                        "body": (data.get("selftext", "") or data.get("title", ""))[:4000],
                        "date_posted": _unix_to_iso(data.get("created_utc")),
                        "engagement": int(data.get("score") or 0),
                    })
        return posts[:limit]


class JobBoardsSource(BaseSource):
    """Hiring signals = budget signals. WWR RSS, RemoteOK API, HN Who-is-Hiring."""

    name = "job_boards"
    WWR_RSS = "https://weworkremotely.com/remote-jobs.rss"
    REMOTEOK_API = "https://remoteok.com/api"
    HN_API = "https://hn.algolia.com/api/v1"

    def collect(self, queries: list[str], limit: int) -> list[dict]:
        posts: list[dict] = []
        for fetch in (self._weworkremotely, self._remoteok, self._hn_hiring):
            if len(posts) >= limit:
                break
            try:
                posts.extend(fetch(limit - len(posts)))
            except Exception as exc:
                logger.warning("job board feed %s failed: %s", fetch.__name__, exc)
        return posts[:limit]

    def _weworkremotely(self, limit: int) -> list[dict]:
        resp = self._get(self.WWR_RSS)
        feed = feedparser.parse(resp.content)
        posts = []
        for entry in feed.entries[:limit]:
            published = entry.get("published_parsed")
            date_iso = datetime(*published[:6], tzinfo=UTC).isoformat() if published else None
            posts.append({
                "source": self.name,
                "url": entry.get("link", ""),
                "title": entry.get("title", ""),
                "body": strip_html(entry.get("summary", ""))[:4000],
                "date_posted": date_iso,
                "engagement": 0,
            })
        return posts

    def _remoteok(self, limit: int) -> list[dict]:
        resp = self._get(self.REMOTEOK_API)
        jobs = resp.json()
        posts = []
        for job in jobs[1:limit + 1]:  # first element is a legal notice
            if not isinstance(job, dict):
                continue
            posts.append({
                "source": self.name,
                "url": job.get("url", ""),
                "title": job.get("position", ""),
                "body": strip_html(job.get("description", ""))[:4000],
                "date_posted": job.get("date") or _unix_to_iso(job.get("epoch")),
                "engagement": 0,
            })
        return posts

    def _hn_hiring(self, limit: int) -> list[dict]:
        resp = self._get(f"{self.HN_API}/search", params={
            "query": "Ask HN: Who is hiring",
            "tags": "story,author_whoishiring",
            "hitsPerPage": 1,
        })
        hits = resp.json().get("hits", [])
        if not hits:
            return []
        story_id = hits[0]["objectID"]
        resp = self._get(f"{self.HN_API}/items/{story_id}")
        children = resp.json().get("children", [])
        posts = []
        for child in children[:limit]:
            text = strip_html(child.get("text") or "")
            if not text:
                continue
            posts.append({
                "source": self.name,
                "url": f"https://news.ycombinator.com/item?id={child['id']}",
                "title": text.split("|")[0][:200],
                "body": text[:4000],
                "date_posted": child.get("created_at"),
                "engagement": 0,
            })
        return posts


class YCRFSSource(BaseSource):
    """Y Combinator Requests for Startups — static scrape with a 5s delay."""

    name = "yc_rfs"
    URL = "https://www.ycombinator.com/rfs"

    def collect(self, queries: list[str], limit: int) -> list[dict]:
        resp = self._get(self.URL)
        soup = BeautifulSoup(resp.text, "html.parser")
        posts = []
        for heading in soup.find_all(["h2", "h3"]):
            title = heading.get_text(" ", strip=True)
            if len(title) < 8:
                continue
            parts = []
            for sibling in heading.find_next_siblings():
                if sibling.name in ("h2", "h3"):
                    break
                parts.append(sibling.get_text(" ", strip=True))
            body = " ".join(p for p in parts if p)[:4000]
            if not body:
                continue
            posts.append({
                "source": self.name,
                "url": f"{self.URL}#{len(posts)}",
                "title": title,
                "body": body,
                "date_posted": None,
                "engagement": 0,
            })
        if not posts:
            text = soup.get_text(" ", strip=True)[:4000]
            if text:
                posts.append({
                    "source": self.name, "url": self.URL,
                    "title": "YC Requests for Startups", "body": text,
                    "date_posted": None, "engagement": 0,
                })
        return posts[:limit]


def build_sources(names: list[str], settings) -> list[BaseSource]:
    """Instantiate enabled sources. X/Facebook come from advanced_scraper."""
    from scraper.advanced_scraper import FacebookSource, XSource

    delay = settings.request_delay_seconds
    timeout = settings.http_timeout_seconds
    registry = {
        "hacker_news": lambda: HackerNewsSource(delay=delay, timeout=timeout),
        "reddit": lambda: RedditSource(delay=delay, timeout=timeout),
        "job_boards": lambda: JobBoardsSource(delay=delay, timeout=timeout),
        "yc_rfs": lambda: YCRFSSource(delay=settings.yc_delay_seconds, timeout=timeout),
        "x": lambda: XSource(settings),
        "facebook": lambda: FacebookSource(settings),
    }
    sources = []
    for name in names:
        if name not in registry:
            logger.warning("build_sources: unknown source %r skipped", name)
            continue
        sources.append(registry[name]())
    return sources