"""
Data Ingestion Pipeline
-----------------------
Sources:
  1. MockSocialMediaStream  — simulated real-time tweet feed (demo/testing)
  2. FileIngestionService   — JSON or CSV file upload
  3. Manual POST            — via /ingest API endpoint

Runs as an async background task; dispatches every post to a
configurable async callback for analysis + storage.
"""

import asyncio
import csv
import io
import json
import logging
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Mock post pool
# ---------------------------------------------------------------------------

MOCK_POSTS: list[dict] = [
    # Disasters
    {"text": "URGENT: Major earthquake hits downtown Istanbul! 6.8 magnitude. Buildings collapsing, people trapped. SOS!", "platform": "twitter", "author": "@istanbulreporter"},
    {"text": "Flash flooding in New Orleans. Water rising fast on Magazine St. Families stranded on rooftops. NEED RESCUE NOW! #NOLAFlood", "platform": "twitter", "author": "@nolalocal"},
    {"text": "Wildfire out of control near Sacramento. Highway 50 closed. Residents of El Dorado Hills EVACUATE IMMEDIATELY.", "platform": "facebook", "author": "CalFireAlerts"},
    {"text": "Gas explosion in Chicago industrial district. Multiple casualties reported. Area cordoned off for 1 mile.", "platform": "twitter", "author": "@chicagonews"},
    {"text": "Tornado EF-3 spotted near Oklahoma City moving NE at 45mph. TAKE SHELTER NOW. #OKCTornado", "platform": "twitter", "author": "@okweatheralert"},
    {"text": "Tsunami warning issued for Pacific coast after 7.2 earthquake offshore. All coastal residents move to higher ground IMMEDIATELY!", "platform": "twitter", "author": "@pacificwarnings"},
    {"text": "Hurricane Category 4 making landfall in Miami. 150mph winds. 15ft storm surge. Mandatory evacuation in effect.", "platform": "facebook", "author": "MiamiEmergency"},
    {"text": "Building collapse in downtown Bangalore. 3 floors down. 12 people trapped inside. Rescue teams needed ASAP!", "platform": "twitter", "author": "@bangalorealert"},
    {"text": "Severe flooding in Mumbai suburbs. Train services suspended. Roads completely submerged. Stay indoors.", "platform": "twitter", "author": "@mumbainews"},
    {"text": "Chemical plant fire in Houston Ship Channel. Toxic fumes. Residents within 5 miles shelter in place.", "platform": "twitter", "author": "@houstonfire"},
    {"text": "Landslide blocks mountain highway in Colorado. Several vehicles buried. Search and rescue deployed.", "platform": "twitter", "author": "@coloradodot"},
    {"text": "Massive bushfire in Victoria, Australia. 10,000+ hectares. 200 homes destroyed. People unaccounted for.", "platform": "facebook", "author": "AustraliaFire"},
    {"text": "Dam breach warning near Sacramento River. Residents downstream evacuate immediately. #DamBreak", "platform": "twitter", "author": "@calemergency"},
    {"text": "7.1 earthquake struck Nepal. Kathmandu severely hit. Infrastructure damage widespread. International aid needed urgently.", "platform": "twitter", "author": "@nepalnews"},
    # Non-disasters
    {"text": "Beautiful sunset over the Golden Gate Bridge today! San Francisco never disappoints.", "platform": "twitter", "author": "@sflocal"},
    {"text": "Just had amazing tacos in Austin TX. Life is good!", "platform": "twitter", "author": "@foodie_austin"},
    {"text": "Traffic slow on I-405 this morning. Left home early — still took 45 mins.", "platform": "twitter", "author": "@ladriver"},
    {"text": "New coffee shop on Main Street is incredible. Their oat milk lattes are fantastic!", "platform": "facebook", "author": "LocalReviewer"},
    {"text": "Great weather in Denver this weekend. Perfect day for hiking Bear Peak.", "platform": "twitter", "author": "@denveroutdoors"},
    {"text": "Anyone watching the championship game tonight? Should be epic!", "platform": "twitter", "author": "@sportsfan99"},
]

_DISASTER_KEYWORDS = {
    "flood", "earthquake", "fire", "tornado", "hurricane", "tsunami",
    "collapse", "explosion", "evacuate", "rescue", "sos", "urgent",
    "wildfire", "landslide", "dam", "breach", "casualties", "trapped",
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class IngestionConfig:
    interval_seconds: float = 6.0
    randomize_interval: bool = True
    jitter_range: float = 4.0
    disaster_ratio: float = 0.65
    max_posts: Optional[int] = None


@dataclass
class IngestedPost:
    text: str
    platform: str = "unknown"
    author: Optional[str] = None
    post_id: Optional[str] = None
    image_url: Optional[str] = None
    ingested_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


@dataclass
class IngestionStatus:
    is_running: bool = False
    posts_processed: int = 0
    disasters_detected: int = 0
    started_at: Optional[str] = None
    stopped_at: Optional[str] = None


# ---------------------------------------------------------------------------
# Mock stream
# ---------------------------------------------------------------------------

class MockSocialMediaStream:
    """Yields realistic mock posts at configurable intervals."""

    def __init__(self, config: IngestionConfig) -> None:
        self.config = config
        self._disaster_pool = [p for p in MOCK_POSTS if any(kw in p["text"].lower() for kw in _DISASTER_KEYWORDS)]
        self._normal_pool = [p for p in MOCK_POSTS if not any(kw in p["text"].lower() for kw in _DISASTER_KEYWORDS)]
        self._counter = 0

    def next_post(self) -> IngestedPost:
        if random.random() < self.config.disaster_ratio and self._disaster_pool:
            raw = random.choice(self._disaster_pool)
        else:
            raw = random.choice(self._normal_pool)
        self._counter += 1
        return IngestedPost(
            text=raw["text"],
            platform=raw.get("platform", "twitter"),
            author=raw.get("author"),
            post_id=f"mock-{self._counter:06d}",
        )

    def sleep_duration(self) -> float:
        dur = self.config.interval_seconds
        if self.config.randomize_interval:
            dur += random.uniform(-self.config.jitter_range, self.config.jitter_range)
        return max(1.0, dur)


# ---------------------------------------------------------------------------
# File ingestion
# ---------------------------------------------------------------------------

class FileIngestionService:
    """Parse JSON or CSV files of social media posts."""

    @staticmethod
    def from_json(content: str | bytes) -> list[IngestedPost]:
        """
        Expected: list of {"text":..., "platform":..., "author":...}
        Single dict also accepted.
        """
        if isinstance(content, bytes):
            content = content.decode("utf-8")
        data = json.loads(content)
        if isinstance(data, dict):
            data = [data]
        posts = []
        for item in data:
            if "text" not in item or not item["text"].strip():
                continue
            posts.append(IngestedPost(
                text=item["text"],
                platform=item.get("platform", item.get("source_platform", "unknown")),
                author=item.get("author"),
                post_id=item.get("post_id", item.get("id")),
                image_url=item.get("image_url"),
            ))
        return posts

    @staticmethod
    def from_csv(content: str | bytes) -> list[IngestedPost]:
        """Columns: text, [platform], [author], [post_id], [image_url]"""
        if isinstance(content, bytes):
            content = content.decode("utf-8")
        reader = csv.DictReader(io.StringIO(content))
        posts = []
        for row in reader:
            if "text" not in row or not row["text"].strip():
                continue
            posts.append(IngestedPost(
                text=row["text"],
                platform=row.get("platform", "unknown"),
                author=row.get("author"),
                post_id=row.get("post_id"),
                image_url=row.get("image_url"),
            ))
        return posts


# ---------------------------------------------------------------------------
# Pipeline orchestrator
# ---------------------------------------------------------------------------

PostCallback = Callable[[IngestedPost], Awaitable[None]]


class IngestionPipeline:
    """
    Orchestrates ingestion and dispatches every post to an async callback.

    Usage
    -----
    pipeline = IngestionPipeline()
    pipeline.set_callback(analyze_and_store)
    await pipeline.start()
    # later...
    await pipeline.stop()
    """

    def __init__(self, config: Optional[IngestionConfig] = None) -> None:
        self.config = config or IngestionConfig()
        self._stream = MockSocialMediaStream(self.config)
        self._task: Optional[asyncio.Task] = None
        self.on_post: Optional[PostCallback] = None
        self.status = IngestionStatus()

    def set_callback(self, cb: PostCallback) -> None:
        self.on_post = cb

    async def start(self) -> bool:
        """Start background stream. Returns False if already running."""
        if self._task and not self._task.done():
            return False
        self.status = IngestionStatus(
            is_running=True,
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        self._task = asyncio.create_task(self._run_stream(), name="ingestion-stream")
        logger.info("Ingestion pipeline started (%.1fs interval).", self.config.interval_seconds)
        return True

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self.status.is_running = False
        self.status.stopped_at = datetime.now(timezone.utc).isoformat()
        logger.info("Ingestion stopped | processed=%d disasters=%d",
                    self.status.posts_processed, self.status.disasters_detected)

    async def ingest_posts(self, posts: list[IngestedPost]) -> int:
        """Manually push a batch (from file or API). Returns count processed."""
        processed = 0
        for post in posts:
            if self.on_post:
                try:
                    await self.on_post(post)
                    processed += 1
                    self.status.posts_processed += 1
                except Exception as exc:
                    logger.error("Error on manual post: %s", exc)
        return processed

    async def _run_stream(self) -> None:
        count = 0
        try:
            while True:
                if self.config.max_posts and count >= self.config.max_posts:
                    self.status.is_running = False
                    break

                post = self._stream.next_post()
                self.status.posts_processed += 1
                count += 1

                if self.on_post:
                    try:
                        await self.on_post(post)
                    except Exception as exc:
                        logger.error("Callback error on %s: %s", post.post_id, exc)

                await asyncio.sleep(self._stream.sleep_duration())
        except asyncio.CancelledError:
            pass
        finally:
            self.status.is_running = False
