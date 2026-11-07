
import httpx
import pytest

from scraper.advanced_scraper import AdvancedScraper, FacebookSource, XSource
from scraper.base import SourceUnavailable
from scraper.sources import (
    HackerNewsSource,
    JobBoardsSource,
    RedditSource,
    YCRFSSource,
    build_sources,
)


def client_with(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_hacker_news_parses_algolia_hits():
    def handler(request):
        assert "hn.algolia.com" in str(request.url)
        return httpx.Response(200, json={"hits": [{
            "objectID": "123", "title": "Ask HN: no API?",
            "story_text": "<p>I copy data manually every day.</p>",
            "points": 42, "created_at": "2026-11-01T00:00:00Z",
        }]})

    source = HackerNewsSource(client=client_with(handler), delay=0)
    posts = source.collect(["no api"], limit=5)
    assert len(posts) == 1
    assert posts[0]["url"] == "https://news.ycombinator.com/item?id=123"
    assert posts[0]["engagement"] == 42
    assert "<p>" not in posts[0]["body"]
    assert posts[0]["source"] == "hacker_news"


def test_hacker_news_query_failure_is_isolated():
    def handler(request):
        if "bad" in str(request.url):
            raise httpx.ConnectError("network down")
        return httpx.Response(200, json={"hits": [{
            "objectID": "9", "title": "good hit", "story_text": "", "points": 1,
            "created_at": "2026-11-01T00:00:00Z",
        }]})

    source = HackerNewsSource(client=client_with(handler), delay=0)
    posts = source.collect(["bad", "good"], limit=5)
    assert len(posts) == 1
    assert posts[0]["title"] == "good hit"


def test_reddit_parses_public_json():
    def handler(request):
        sub = request.url.path.split("/")[2]
        return httpx.Response(200, json={"data": {"children": [{
            "data": {"permalink": f"/r/{sub}/comments/x/", "title": "Manual exports hurt",
                     "selftext": "No API, so we export by hand.", "score": 17,
                     "created_utc": 1793880000},
        }]}})

    source = RedditSource(client=client_with(handler), delay=0)
    posts = source.collect(["manual exports"], limit=5)
    assert len(posts) == 3  # one per subreddit
    assert posts[0]["url"].startswith("https://www.reddit.com/r/SaaS/")
    assert posts[0]["engagement"] == 17


def test_reddit_429_stops_politely():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429)

    source = RedditSource(client=client_with(handler), delay=0)
    posts = source.collect(["q1", "q2", "q3"], limit=50)
    assert posts == []
    assert len(calls) == 1  # stopped after first rate-limit


def test_wwr_rss_parsing():
    rss = """<?xml version="1.0"?><rss version="2.0"><channel>
    <item><title>Scraping Engineer at Acme</title>
    <link>https://weworkremotely.com/remote-jobs/1</link>
    <description>&lt;p&gt;Build extraction pipelines, no API available.&lt;/p&gt;</description>
    <pubDate>Mon, 02 Nov 2026 00:00:00 +0000</pubDate></item>
    </channel></rss>"""

    def handler(request):
        return httpx.Response(200, text=rss)

    source = JobBoardsSource(client=client_with(handler), delay=0)
    posts = source._weworkremotely(5)
    assert len(posts) == 1
    assert posts[0]["title"] == "Scraping Engineer at Acme"
    assert "no API" in posts[0]["body"]


def test_remoteok_api_parsing():
    def handler(request):
        return httpx.Response(200, json=[
            {"legal": "notice"},
            {"position": "Data Engineer", "url": "https://remoteok.com/j/1",
             "description": "<b>Extract data</b> from portals", "epoch": 1793880000},
        ])

    source = JobBoardsSource(client=client_with(handler), delay=0)
    posts = source._remoteok(5)
    assert len(posts) == 1
    assert posts[0]["title"] == "Data Engineer"
    assert "<b>" not in posts[0]["body"]


def test_hn_hiring_comments():
    def handler(request):
        if "/search" in str(request.url):
            return httpx.Response(200, json={"hits": [{"objectID": "555"}]})
        return httpx.Response(200, json={"children": [{
            "id": 556, "text": "Acme | Scraping Engineer | Remote | $150k<br>Build pipelines",
            "created_at": "2026-11-01T00:00:00Z",
        }]})

    source = JobBoardsSource(client=client_with(handler), delay=0)
    posts = source._hn_hiring(5)
    assert len(posts) == 1
    assert posts[0]["url"] == "https://news.ycombinator.com/item?id=556"
    assert "Acme" in posts[0]["title"]


def test_yc_rfs_static_parse():
    html = """<html><body>
    <h2>Automatic Data Extraction</h2><p>Businesses do manual data entry from PDFs.</p>
    <h2>Another Request</h2><p>Replace repetitive workflows.</p>
    </body></html>"""

    def handler(request):
        return httpx.Response(200, html=html)

    source = YCRFSSource(client=client_with(handler), delay=0)
    posts = source.collect([], limit=10)
    assert len(posts) == 2
    assert posts[0]["title"] == "Automatic Data Extraction"
    assert posts[0]["source"] == "yc_rfs"


def test_x_requires_cookies(settings):
    scraper = AdvancedScraper(settings)
    assert not scraper.x_configured()
    with pytest.raises(SourceUnavailable):
        scraper.search_x("no api")


def test_x_headers_use_session_cookies(settings):
    settings.x_auth_token = "tok123"
    settings.x_ct0 = "csrf456"
    scraper = AdvancedScraper(settings)
    headers = scraper._x_headers()
    assert headers["x-csrf-token"] == "csrf456"
    assert "auth_token=tok123" in headers["cookie"]
    assert "ct0=csrf456" in headers["cookie"]
    assert headers["authorization"].startswith("Bearer ")


def test_x_graphql_search_parsing(settings):
    settings.x_auth_token = "tok"
    settings.x_ct0 = "csrf"
    tweet = {"legacy": {
        "id_str": "42", "full_text": "No API, manual copy paste every week",
        "favorite_count": 10, "created_at": "Mon Nov 02 00:00:00 +0000 2026",
    }}
    entry = {"content": {"itemContent": {"tweet_results": {"result": tweet}}}}
    payload = {"data": {"search_by_raw_query": {"search_timeline": {"timeline": {
        "instructions": [{"entries": [entry]}],
    }}}}}

    def handler(request):
        assert "/SearchTimeline" in str(request.url)
        assert request.headers["x-csrf-token"] == "csrf"
        return httpx.Response(200, json=payload)

    source = XSource(settings, client=client_with(handler))
    posts = source.collect(["no api"], limit=5)
    assert len(posts) == 1
    assert posts[0]["url"] == "https://x.com/i/web/status/42"
    assert posts[0]["engagement"] == 10


def test_facebook_requires_cookie(settings):
    scraper = AdvancedScraper(settings)
    assert not scraper.facebook_configured()
    with pytest.raises(SourceUnavailable):
        scraper.search_facebook("manual reporting")


def test_facebook_tolerant_parsing(settings):
    settings.facebook_cookie = "c_user=1; xs=secret"
    payload = {"data": {"search": {"edges": [{"node": {
        "message": {"text": "Manual screenshots every Friday for clients"},
        "url": "https://www.facebook.com/groups/x/posts/1",
    }}]}}}

    def handler(request):
        assert request.method == "POST"
        assert "c_user=1" in request.headers["cookie"]
        return httpx.Response(200, json=payload)

    source = FacebookSource(settings, client=client_with(handler))
    posts = source.collect(["reporting"], limit=5)
    assert len(posts) == 1
    assert posts[0]["source"] == "facebook"
    assert "Manual screenshots" in posts[0]["body"]


def test_build_sources_registry(settings):
    sources = build_sources(["hacker_news", "reddit", "nonexistent"], settings)
    assert [s.name for s in sources] == ["hacker_news", "reddit"]
    for source in sources:
        source.close()