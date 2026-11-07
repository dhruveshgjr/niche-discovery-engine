"""Custom fast extraction engine: real session cookies -> internal GraphQL APIs.

No Selenium/Playwright. X and Facebook are scraped the same way their own web
clients work: authenticated session cookies + the same GraphQL endpoints the
browser calls. Credentials come from .env; without them the sources are skipped
gracefully (SourceUnavailable) and the pipeline continues.

GraphQL query/doc IDs rotate server-side. If a request starts failing, copy the
fresh ID from your browser's Network tab into X_GRAPHQL_QUERY_ID /
FACEBOOK_DOC_ID (see README).
"""
from __future__ import annotations

import json
import time

import httpx

from config import Settings
from logging_setup import get_logger
from scraper.base import BaseSource, SourceUnavailable

logger = get_logger(__name__)

BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# Public bearer embedded in the x.com web client (not a secret credential).
X_WEB_BEARER = (
    "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D"
    "1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
)

# Defaults rotate over time; override via .env when stale.
DEFAULT_X_QUERY_ID = "nK1dw4oV3k4w5TdtcAdSww"
DEFAULT_FB_DOC_ID = "23976198158639421"

X_FEATURES = {
    "rweb_tipjar_consumption_enabled": True,
    "responsive_web_graphql_exclude_directive_enabled": True,
    "verified_phone_label_enabled": False,
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "responsive_web_graphql_timeline_navigation_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "communities_web_enable_tweet_community_results_fetch": True,
    "c9s_tweet_anatomy_moderator_badge_enabled": True,
    "tweetypie_unmention_optimization_enabled": True,
    "responsive_web_edit_tweet_api_enabled": True,
    "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
    "view_counts_everywhere_api_enabled": True,
    "longform_notetweets_consumption_enabled": True,
    "responsive_web_twitter_article_tweet_consumption_enabled": True,
    "tweet_awards_web_tipping_enabled": False,
    "freedom_of_speech_not_reach_fetch_enabled": True,
    "standardized_nudges_misinfo": True,
    "longform_notetweets_rich_text_read_enabled": True,
    "longform_notetweets_inline_media_enabled": True,
    "responsive_web_enhance_cards_enabled": False,
}


class AdvancedScraper(BaseSource):
    name = "advanced"

    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        super().__init__(
            client=client,
            delay=settings.request_delay_seconds,
            timeout=settings.http_timeout_seconds,
        )
        self.settings = settings

    # --- X / Twitter ---

    def x_configured(self) -> bool:
        return bool(self.settings.x_auth_token and self.settings.x_ct0)

    def _x_headers(self) -> dict:
        return {
            "authorization": f"Bearer {X_WEB_BEARER}",
            "x-csrf-token": self.settings.x_ct0,
            "cookie": f"auth_token={self.settings.x_auth_token}; ct0={self.settings.x_ct0}",
            "user-agent": BROWSER_UA,
            "content-type": "application/json",
        }

    def search_x(self, query: str, limit: int = 20) -> list[dict]:
        if not self.x_configured():
            raise SourceUnavailable("X cookies (X_AUTH_TOKEN, X_CT0) not configured")
        query_id = self.settings.x_graphql_query_id or DEFAULT_X_QUERY_ID
        resp = self._get(
            f"https://x.com/i/api/graphql/{query_id}/SearchTimeline",
            params={
                "variables": json.dumps({
                    "rawQuery": query, "count": min(limit, 20),
                    "product": "Latest", "querySource": "typed_query",
                }),
                "features": json.dumps(X_FEATURES),
            },
            headers=self._x_headers(),
        )
        return self._parse_x_search(resp.json(), limit)

    @staticmethod
    def _parse_x_search(payload: dict, limit: int) -> list[dict]:
        instructions = (
            payload.get("data", {})
            .get("search_by_raw_query", {})
            .get("search_timeline", {})
            .get("timeline", {})
            .get("instructions", [])
        )
        posts = []
        for instruction in instructions:
            for entry in instruction.get("entries", []):
                result = (
                    entry.get("content", {})
                    .get("itemContent", {})
                    .get("tweet_results", {})
                    .get("result", {})
                )
                legacy = result.get("legacy") or result.get("tweet", {}).get("legacy")
                if not legacy:
                    continue
                tweet_id = legacy.get("id_str") or result.get("rest_id")
                if not tweet_id:
                    continue
                posts.append({
                    "source": "x",
                    "url": f"https://x.com/i/web/status/{tweet_id}",
                    "title": legacy.get("full_text", "")[:120],
                    "body": legacy.get("full_text", "")[:4000],
                    "date_posted": legacy.get("created_at"),
                    "engagement": int(legacy.get("favorite_count") or 0),
                })
                if len(posts) >= limit:
                    return posts
        return posts

    # --- Facebook ---

    def facebook_configured(self) -> bool:
        return bool(self.settings.facebook_cookie)

    def search_facebook(self, query: str, limit: int = 20) -> list[dict]:
        if not self.facebook_configured():
            raise SourceUnavailable("FACEBOOK_COOKIE not configured")
        if self._delay:
            time.sleep(self._delay)
        resp = self._client.post(
            "https://www.facebook.com/api/graphql/",
            data={
                "doc_id": self.settings.facebook_doc_id or DEFAULT_FB_DOC_ID,
                "variables": json.dumps({"keyword_query": query}),
                "server_timestamps": "true",
            },
            headers={
                "cookie": self.settings.facebook_cookie,
                "user-agent": BROWSER_UA,
                "content-type": "application/x-www-form-urlencoded",
            },
        )
        resp.raise_for_status()
        return self._parse_facebook_search(resp.json(), limit)

    @staticmethod
    def _parse_facebook_search(payload: dict, limit: int) -> list[dict]:
        """Tolerant walk: FB search shapes vary; collect every story message+url pair."""
        posts = []
        seen = set()

        def walk(node):
            if len(posts) >= limit:
                return
            if isinstance(node, dict):
                message = node.get("message")
                if isinstance(message, dict) and message.get("text"):
                    url = node.get("url") or node.get("wwwURL") or ""
                    text = message["text"]
                    key = url or text[:80]
                    if key and key not in seen:
                        seen.add(key)
                        posts.append({
                            "source": "facebook",
                            "url": url or "https://www.facebook.com",
                            "title": text[:120],
                            "body": text[:4000],
                            "date_posted": None,
                            "engagement": 0,
                        })
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(payload)
        return posts


class XSource(BaseSource):
    name = "x"

    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        self.scraper = AdvancedScraper(settings, client)

    def collect(self, queries: list[str], limit: int) -> list[dict]:
        posts: list[dict] = []
        for query in queries[:5]:
            if len(posts) >= limit:
                break
            posts.extend(self.scraper.search_x(query, limit - len(posts)))
        return posts[:limit]

    def close(self) -> None:
        self.scraper.close()


class FacebookSource(BaseSource):
    name = "facebook"

    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        self.scraper = AdvancedScraper(settings, client)

    def collect(self, queries: list[str], limit: int) -> list[dict]:
        posts: list[dict] = []
        for query in queries[:5]:
            if len(posts) >= limit:
                break
            posts.extend(self.scraper.search_facebook(query, limit - len(posts)))
        return posts[:limit]

    def close(self) -> None:
        self.scraper.close()