"""
Real Social Media & News Ingestion  (v2)
-----------------------------------------
Active sources (enabled when relevant keys are set in keysss.env):

  Platform         | Env key(s) needed              | Status
  ─────────────────┼────────────────────────────────┼───────────────────
  Twitter / X      | TWITTER_BEARER_TOKEN           | real API (tweepy v2)
  Reddit           | (none — public JSON API)       | real API, no auth
  GDACS RSS        | (none)                         | UN official alerts
  USGS Earthquakes | (none)                         | seismic atom feed
  NOAA/NWS         | (none)                         | US weather CAP feed
  ReliefWeb (OCHA) | (none)                         | humanitarian alerts
  FEMA             | (none)                         | US federal disasters
  NewsData.io      | NEWSDATA_API_KEY               | news API
  NewsAPI.org      | NEWSAPI_KEY                    | news API
  Facebook         | FB_PAGE_TOKEN + FB_PAGE_ID     | Graph API (page posts)
  Instagram        | IG_USER_TOKEN + IG_USER_ID     | Graph API (business)
  Threads          | THREADS_TOKEN + THREADS_USER_ID| Meta Threads API

Facebook / Instagram / Threads notes
-------------------------------------
Meta's APIs require an approved developer app and either a Page Access Token
(Facebook), a Business/Creator account token (Instagram), or a Threads user
token.  The connectors below are fully implemented but will silently no-op if
the corresponding env vars are absent.  To activate them:
  1. Create a Meta Developer app at https://developers.facebook.com/
  2. Add the required permissions (pages_read_engagement, instagram_basic, etc.)
  3. Complete App Review for any public-data permission
  4. Set the tokens in keysss.env

All sources convert to IngestedPost and feed into the existing callback.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

import feedparser
import httpx
from dotenv import load_dotenv

from nlp.data_ingestion import IngestedPost, IngestionStatus

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", "keysss.env"))

logger = logging.getLogger(__name__)

# ── API credentials ──────────────────────────────────────────────────────────
NEWSDATA_API_KEY    = os.getenv("NEWSDATA_API_KEY", "")
NEWSAPI_KEY         = os.getenv("NEWSAPI_KEY", "")
TWITTER_BEARER_TOKEN = os.getenv("TWITTER_BEARER_TOKEN", "")
FB_PAGE_TOKEN       = os.getenv("FB_PAGE_TOKEN", "")
FB_PAGE_ID          = os.getenv("FB_PAGE_ID", "")
IG_USER_TOKEN       = os.getenv("IG_USER_TOKEN", "")
IG_USER_ID          = os.getenv("IG_USER_ID", "")
THREADS_TOKEN       = os.getenv("THREADS_TOKEN", "")
THREADS_USER_ID     = os.getenv("THREADS_USER_ID", "")

# ── Disaster keyword list ─────────────────────────────────────────────────────
DISASTER_KEYWORDS = [
    "earthquake", "flood", "wildfire", "hurricane", "tornado", "tsunami",
    "explosion", "landslide", "cyclone", "disaster", "emergency", "evacuation",
    "rescue", "casualties", "collapse", "fire", "storm", "drought", "blizzard",
    "avalanche", "volcanic", "eruption", "displaced", "relief", "aid",
]

# ── Endpoint URLs ─────────────────────────────────────────────────────────────
NEWSDATA_URL   = "https://newsdata.io/api/1/news"
NEWSAPI_URL    = "https://newsapi.org/v2/everything"
REDDIT_SEARCH  = "https://www.reddit.com/r/{subs}/search.json"
REDDIT_SUBS    = "worldnews+news+naturaldisasters+floods+earthquakes+collapse"

RSS_FEEDS: dict[str, str] = {
    "gdacs.org":   "https://www.gdacs.org/xml/rss.xml",
    "usgs":        "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/significant_week.atom",
    "noaa":        "https://alerts.weather.gov/cap/us.php?x=1",
    "reliefweb":   "https://reliefweb.int/disasters/rss.xml",
    "fema":        "https://www.fema.gov/feeds/en/disasters.rss",
}

# ── Poll intervals (seconds) ──────────────────────────────────────────────────
TWITTER_INTERVAL  = 120
REDDIT_INTERVAL   = 60
RSS_INTERVAL      = 120
NEWSDATA_INTERVAL = 60
NEWSAPI_INTERVAL  = 90
FB_INTERVAL       = 120
IG_INTERVAL       = 120
THREADS_INTERVAL  = 120

PostCallback = Callable[[IngestedPost], Awaitable[None]]


# ── Deduplication cache ───────────────────────────────────────────────────────

class _DedupeCache:
    """In-memory set of recently seen content hashes (last 3000 entries)."""

    def __init__(self, maxsize: int = 3000) -> None:
        self._seen: dict[str, bool] = {}
        self._maxsize = maxsize

    def is_new(self, text: str) -> bool:
        h = hashlib.md5(text.strip().lower().encode()).hexdigest()
        if h in self._seen:
            return False
        if len(self._seen) >= self._maxsize:
            keys = list(self._seen)
            for k in keys[: self._maxsize // 2]:
                del self._seen[k]
        self._seen[h] = True
        return True


_cache = _DedupeCache()


# ─────────────────────────────────────────────────────────────────────────────
# TWITTER / X  (tweepy v2)
# ─────────────────────────────────────────────────────────────────────────────

def _twitter_search_sync() -> list[IngestedPost]:
    """Synchronous Twitter search — run in executor."""
    try:
        import tweepy
    except ImportError:
        logger.warning("tweepy not installed — Twitter ingestion disabled.")
        return []

    query = " OR ".join(DISASTER_KEYWORDS[:8]) + " lang:en -is:retweet"
    client = tweepy.Client(bearer_token=TWITTER_BEARER_TOKEN, wait_on_rate_limit=False)
    try:
        response = client.search_recent_tweets(
            query=query,
            max_results=10,
            tweet_fields=["author_id", "created_at", "entities", "geo"],
            expansions=["author_id"],
            user_fields=["username", "verified"],
        )
    except Exception as exc:
        logger.warning("Twitter API error: %s", exc)
        return []

    if not response.data:
        return []

    users: dict = {}
    if response.includes and response.includes.get("users"):
        users = {u.id: u for u in response.includes["users"]}

    posts: list[IngestedPost] = []
    for tweet in response.data:
        text = tweet.text or ""
        if not text or not _cache.is_new(text):
            continue
        author = None
        if tweet.author_id and tweet.author_id in users:
            author = f"@{users[tweet.author_id].username}"
        posts.append(IngestedPost(
            text=text,
            platform="twitter",
            author=author,
            post_id=str(tweet.id),
        ))
    logger.info("Twitter: fetched %d new tweets", len(posts))
    return posts


async def _fetch_twitter(_: httpx.AsyncClient) -> list[IngestedPost]:
    """Async wrapper — runs synchronous tweepy call in thread executor."""
    if not TWITTER_BEARER_TOKEN:
        return []
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(None, _twitter_search_sync)
    except Exception as exc:
        logger.warning("Twitter fetch failed: %s", exc)
        return []


# ─────────────────────────────────────────────────────────────────────────────
# REDDIT  (public JSON API — no auth required)
# ─────────────────────────────────────────────────────────────────────────────

async def _fetch_reddit(client: httpx.AsyncClient) -> list[IngestedPost]:
    """
    Polls Reddit's public JSON search across disaster-focused subreddits.
    No authentication required; uses a descriptive User-Agent per Reddit policy.
    """
    query = " OR ".join(DISASTER_KEYWORDS[:8])
    try:
        resp = await client.get(
            REDDIT_SEARCH.format(subs=REDDIT_SUBS),
            params={
                "q": query,
                "sort": "new",
                "t": "hour",
                "limit": 25,
                "restrict_sr": "true",
                "type": "link",
            },
            headers={"User-Agent": "DisasterAlertSystem/2.0 (monitoring bot; contact: admin@example.com)"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        posts: list[IngestedPost] = []
        for child in data.get("data", {}).get("children", []):
            pd = child.get("data", {})
            title    = pd.get("title", "")
            selftext = pd.get("selftext", "")
            full     = f"{title}. {selftext}".strip(". ") if selftext else title
            if not full or not _cache.is_new(full):
                continue
            # Filter for disaster relevance
            if not any(kw in full.lower() for kw in DISASTER_KEYWORDS):
                continue
            url_val = pd.get("url", "")
            img = url_val if url_val.lower().endswith((".jpg", ".jpeg", ".png", ".webp")) else None
            posts.append(IngestedPost(
                text=full,
                platform="reddit",
                author=f"u/{pd.get('author', 'unknown')}",
                post_id=pd.get("id"),
                image_url=img,
            ))
        logger.info("Reddit: fetched %d new posts", len(posts))
        return posts
    except Exception as exc:
        logger.warning("Reddit fetch failed: %s", exc)
        return []


# ─────────────────────────────────────────────────────────────────────────────
# RSS FEEDS  (GDACS, USGS, NOAA, ReliefWeb, FEMA — no auth needed)
# ─────────────────────────────────────────────────────────────────────────────

async def _fetch_rss_feed(client: httpx.AsyncClient, source_id: str, url: str) -> list[IngestedPost]:
    """Download and parse an RSS/Atom feed using feedparser."""
    try:
        resp = await client.get(url, timeout=15, follow_redirects=True)
        resp.raise_for_status()
        feed = feedparser.parse(resp.text)
        posts: list[IngestedPost] = []
        for entry in feed.entries:
            title   = entry.get("title", "")
            summary = entry.get("summary", "") or entry.get("description", "")
            # Strip HTML tags from summary
            import re
            summary = re.sub(r"<[^>]+>", " ", summary).strip()
            full    = f"{title}. {summary}".strip(". ") if summary else title
            if not full or not _cache.is_new(full):
                continue
            post_id = entry.get("id") or entry.get("link")
            posts.append(IngestedPost(
                text=full[:1000],
                platform=source_id,
                author=entry.get("author") or source_id.upper(),
                post_id=post_id,
            ))
        logger.info("%s RSS: fetched %d new entries", source_id, len(posts))
        return posts
    except Exception as exc:
        logger.warning("%s RSS fetch failed: %s", source_id, exc)
        return []


async def _fetch_all_rss(client: httpx.AsyncClient) -> list[IngestedPost]:
    """Fetch all RSS feeds concurrently."""
    results = await asyncio.gather(
        *[_fetch_rss_feed(client, sid, url) for sid, url in RSS_FEEDS.items()],
        return_exceptions=True,
    )
    posts: list[IngestedPost] = []
    for r in results:
        if isinstance(r, list):
            posts.extend(r)
    return posts


# ─────────────────────────────────────────────────────────────────────────────
# NewsData.io
# ─────────────────────────────────────────────────────────────────────────────

async def _fetch_newsdata(client: httpx.AsyncClient) -> list[IngestedPost]:
    if not NEWSDATA_API_KEY:
        return []
    try:
        resp = await client.get(
            NEWSDATA_URL,
            params={
                "apikey": NEWSDATA_API_KEY,
                "q": " OR ".join(DISASTER_KEYWORDS[:8]),
                "language": "en",
                "category": "top",
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        posts: list[IngestedPost] = []
        for article in data.get("results", []):
            text = article.get("title", "") or ""
            desc = article.get("description") or ""
            full = f"{text}. {desc}".strip(". ")
            if not full or not _cache.is_new(full):
                continue
            posts.append(IngestedPost(
                text=full,
                platform="newsdata.io",
                author=article.get("source_id", "newsdata"),
                post_id=article.get("article_id"),
                image_url=article.get("image_url"),
            ))
        logger.info("NewsData.io: fetched %d new posts", len(posts))
        return posts
    except Exception as exc:
        logger.warning("NewsData.io fetch failed: %s", exc)
        return []


# ─────────────────────────────────────────────────────────────────────────────
# NewsAPI.org
# ─────────────────────────────────────────────────────────────────────────────

async def _fetch_newsapi(client: httpx.AsyncClient) -> list[IngestedPost]:
    if not NEWSAPI_KEY:
        return []
    try:
        resp = await client.get(
            NEWSAPI_URL,
            params={
                "apiKey": NEWSAPI_KEY,
                "q": " OR ".join(DISASTER_KEYWORDS[:6]),
                "language": "en",
                "sortBy": "publishedAt",
                "pageSize": 20,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        posts: list[IngestedPost] = []
        for article in data.get("articles", []):
            text = article.get("title", "") or ""
            desc = article.get("description") or ""
            full = f"{text}. {desc}".strip(". ")
            if not full or not _cache.is_new(full):
                continue
            source = article.get("source", {}).get("name", "newsapi")
            posts.append(IngestedPost(
                text=full,
                platform="newsapi.org",
                author=source,
                post_id=None,
                image_url=article.get("urlToImage"),
            ))
        logger.info("NewsAPI.org: fetched %d new posts", len(posts))
        return posts
    except Exception as exc:
        logger.warning("NewsAPI.org fetch failed: %s", exc)
        return []


# ─────────────────────────────────────────────────────────────────────────────
# FACEBOOK  (Meta Graph API — requires page access token)
# ─────────────────────────────────────────────────────────────────────────────

async def _fetch_facebook(client: httpx.AsyncClient) -> list[IngestedPost]:
    """
    Fetches posts from a configured Facebook Page using the Graph API.

    Setup:
      1. Create a Meta Developer app at https://developers.facebook.com/
      2. Add 'pages_read_engagement' permission
      3. Generate a Page Access Token (never-expiring via long-lived token exchange)
      4. Set FB_PAGE_TOKEN and FB_PAGE_ID in keysss.env

    Limitation: Can only read from pages where the token owner is an admin.
    For monitoring public emergency pages, the page admin must grant access.
    """
    if not FB_PAGE_TOKEN or not FB_PAGE_ID:
        return []
    try:
        resp = await client.get(
            f"https://graph.facebook.com/v19.0/{FB_PAGE_ID}/posts",
            params={
                "access_token": FB_PAGE_TOKEN,
                "fields": "message,created_time,full_picture,id",
                "limit": 15,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        posts: list[IngestedPost] = []
        for item in data.get("data", []):
            text = item.get("message", "")
            if not text or not _cache.is_new(text):
                continue
            if not any(kw in text.lower() for kw in DISASTER_KEYWORDS):
                continue
            posts.append(IngestedPost(
                text=text,
                platform="facebook",
                author=f"fb/page/{FB_PAGE_ID}",
                post_id=item.get("id"),
                image_url=item.get("full_picture"),
            ))
        logger.info("Facebook: fetched %d new posts", len(posts))
        return posts
    except Exception as exc:
        logger.warning("Facebook fetch failed: %s", exc)
        return []


# ─────────────────────────────────────────────────────────────────────────────
# INSTAGRAM  (Meta Graph API — requires Business/Creator account)
# ─────────────────────────────────────────────────────────────────────────────

async def _fetch_instagram(client: httpx.AsyncClient) -> list[IngestedPost]:
    """
    Fetches media from an Instagram Business or Creator account.

    Setup:
      1. Connect Instagram account to a Facebook Page in Business Manager
      2. Add 'instagram_basic' and 'instagram_content_publish' permissions
      3. Generate a long-lived User Access Token
      4. Set IG_USER_TOKEN and IG_USER_ID in keysss.env

    Limitation: Only reads from accounts you own.  Public hashtag search was
    removed from the API in 2024.  For broad monitoring, use a social
    listening service (Brandwatch, Mention, etc.) or Meta Content Library
    (available to academic/researcher accounts).
    """
    if not IG_USER_TOKEN or not IG_USER_ID:
        return []
    try:
        resp = await client.get(
            f"https://graph.instagram.com/v19.0/{IG_USER_ID}/media",
            params={
                "access_token": IG_USER_TOKEN,
                "fields": "caption,media_type,media_url,timestamp,username",
                "limit": 15,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        posts: list[IngestedPost] = []
        for item in data.get("data", []):
            caption = item.get("caption", "") or ""
            if not caption or not _cache.is_new(caption):
                continue
            if not any(kw in caption.lower() for kw in DISASTER_KEYWORDS):
                continue
            media_url = item.get("media_url") if item.get("media_type") == "IMAGE" else None
            posts.append(IngestedPost(
                text=caption,
                platform="instagram",
                author=f"@{item.get('username', IG_USER_ID)}",
                post_id=item.get("id"),
                image_url=media_url,
            ))
        logger.info("Instagram: fetched %d new posts", len(posts))
        return posts
    except Exception as exc:
        logger.warning("Instagram fetch failed: %s", exc)
        return []


# ─────────────────────────────────────────────────────────────────────────────
# THREADS  (Meta Threads API)
# ─────────────────────────────────────────────────────────────────────────────

async def _fetch_threads(client: httpx.AsyncClient) -> list[IngestedPost]:
    """
    Fetches posts from a Threads account via the Meta Threads API.

    Setup:
      1. Register a Meta Developer app with Threads permissions
      2. Generate a long-lived User Access Token via OAuth
      3. Set THREADS_TOKEN and THREADS_USER_ID in keysss.env

    API docs: https://developers.facebook.com/docs/threads
    Note: The Threads API is currently in limited beta; public search/hashtag
    endpoints are not yet available.  This connector reads from your own
    account's threads, useful if your organisation posts disaster updates there.
    """
    if not THREADS_TOKEN or not THREADS_USER_ID:
        return []
    try:
        resp = await client.get(
            f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads",
            params={
                "access_token": THREADS_TOKEN,
                "fields": "id,text,media_type,media_url,timestamp,username",
                "limit": 20,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        posts: list[IngestedPost] = []
        for item in data.get("data", []):
            text = item.get("text", "") or ""
            if not text or not _cache.is_new(text):
                continue
            if not any(kw in text.lower() for kw in DISASTER_KEYWORDS):
                continue
            img = item.get("media_url") if item.get("media_type") == "IMAGE" else None
            posts.append(IngestedPost(
                text=text,
                platform="threads",
                author=f"@{item.get('username', THREADS_USER_ID)}",
                post_id=item.get("id"),
                image_url=img,
            ))
        logger.info("Threads: fetched %d new posts", len(posts))
        return posts
    except Exception as exc:
        logger.warning("Threads fetch failed: %s", exc)
        return []


# ─────────────────────────────────────────────────────────────────────────────
# STATUS + PIPELINE
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RealIngestionStatus:
    is_running:       bool = False
    posts_processed:  int  = 0
    disasters_detected: int = 0
    started_at:       Optional[str] = None
    stopped_at:       Optional[str] = None
    sources: dict = field(default_factory=lambda: {
        "twitter":     0,
        "reddit":      0,
        "newsdata.io": 0,
        "newsapi.org": 0,
        "gdacs.org":   0,
        "usgs":        0,
        "noaa":        0,
        "reliefweb":   0,
        "fema":        0,
        "facebook":    0,
        "instagram":   0,
        "threads":     0,
    })


class RealIngestionPipeline:
    """
    Polls all real sources on independent intervals and dispatches every new
    post to the same async callback used by the mock pipeline.

    Active sources are detected automatically from env vars — no config change
    needed to add/remove a source, just set or unset the key.
    """

    def __init__(self) -> None:
        self.on_post: Optional[PostCallback] = None
        self.status = RealIngestionStatus()
        self._tasks: list[asyncio.Task] = []
        self._client: Optional[httpx.AsyncClient] = None

    def set_callback(self, cb: PostCallback) -> None:
        self.on_post = cb

    async def start(self) -> bool:
        if self._tasks and any(not t.done() for t in self._tasks):
            return False
        self._client = httpx.AsyncClient(follow_redirects=True)
        self.status = RealIngestionStatus(
            is_running=True,
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        active: list[str] = []

        def _task(name: str, fn, interval: int):
            active.append(name)
            return asyncio.create_task(self._poll_loop(fn, interval), name=name)

        self._tasks = [
            _task("twitter-poll",  lambda: _fetch_twitter(self._client),    TWITTER_INTERVAL),
            _task("reddit-poll",   lambda: _fetch_reddit(self._client),     REDDIT_INTERVAL),
            _task("rss-poll",      lambda: _fetch_all_rss(self._client),    RSS_INTERVAL),
            _task("newsdata-poll", lambda: _fetch_newsdata(self._client),   NEWSDATA_INTERVAL),
            _task("newsapi-poll",  lambda: _fetch_newsapi(self._client),    NEWSAPI_INTERVAL),
            _task("facebook-poll", lambda: _fetch_facebook(self._client),   FB_INTERVAL),
            _task("instagram-poll",lambda: _fetch_instagram(self._client),  IG_INTERVAL),
            _task("threads-poll",  lambda: _fetch_threads(self._client),    THREADS_INTERVAL),
        ]
        logger.info(
            "RealIngestionPipeline started — %d poll loops active. "
            "Twitter=%s Reddit=yes RSS=%d-feeds FB=%s IG=%s Threads=%s",
            len(self._tasks),
            "yes" if TWITTER_BEARER_TOKEN else "no-key",
            len(RSS_FEEDS),
            "yes" if (FB_PAGE_TOKEN and FB_PAGE_ID) else "no-key",
            "yes" if (IG_USER_TOKEN and IG_USER_ID) else "no-key",
            "yes" if (THREADS_TOKEN and THREADS_USER_ID) else "no-key",
        )
        return True

    async def stop(self) -> None:
        for t in self._tasks:
            if not t.done():
                t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._client:
            await self._client.aclose()
        self.status.is_running = False
        self.status.stopped_at = datetime.now(timezone.utc).isoformat()
        logger.info(
            "RealIngestionPipeline stopped | processed=%d",
            self.status.posts_processed,
        )

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _poll_loop(self, fetch_fn, interval: int) -> None:
        """Generic poll loop: call fetch_fn, dispatch results, sleep, repeat."""
        try:
            while True:
                posts = await fetch_fn()
                await self._dispatch(posts)
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("Poll loop crashed: %s", exc, exc_info=True)

    async def _dispatch(self, posts: list[IngestedPost]) -> None:
        if not self.on_post:
            return
        for post in posts:
            try:
                await self.on_post(post)
                self.status.posts_processed += 1
                self.status.sources[post.platform] = (
                    self.status.sources.get(post.platform, 0) + 1
                )
            except Exception as exc:
                logger.error("Dispatch error for post '%s': %s", post.post_id, exc)
