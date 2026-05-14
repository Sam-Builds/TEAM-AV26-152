"""
FastAPI Application — Social Media Disaster Alert System
---------------------------------------------------------

Endpoints
---------
System:
  GET  /health                  health check + model status

Analysis (on-demand):
  POST /analyze                 analyze one post
  POST /analyze/batch           analyze up to 50 posts

Ingestion (background stream):
  POST /ingestion/start         start mock social-media stream
  POST /ingestion/stop          stop the stream
  GET  /ingestion/status        stream counters + state
  POST /ingest                  manually submit one post
  POST /ingest/file             upload JSON or CSV file of posts

Alerts (persisted in SQLite):
  GET  /alerts                  paginated, filterable list
  GET  /alerts/stats            aggregate counts by severity / type
  DELETE /alerts                clear all stored alerts
  GET  /alerts/{alert_id}       single alert detail

Real-time:
  GET  /stream/alerts           Server-Sent Events stream

Webhooks:
  GET  /webhooks                list registered webhooks
  POST /webhooks                register a webhook URL
  DELETE /webhooks/{id}         remove a webhook

Run with:
  python api.py
  OR: uvicorn api:app --reload --host 0.0.0.0 --port 8000
"""

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from nlp.nlp_pipeline import DisasterNLPPipeline
from nlp.vision_analyzer import VisionAnalyzer
from nlp.ensemble_engine import EnsembleEngine
from nlp.models import EnsembleInput, AlertSeverity
from nlp.data_ingestion import IngestionPipeline, IngestionConfig, IngestedPost, FileIngestionService
from nlp.real_ingestion import RealIngestionPipeline, RealIngestionStatus
from nlp.database import AlertDatabase
from alert_notifier import AlertNotifier

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Rate limiter (Gap 9)
limiter = Limiter(key_func=get_remote_address, default_limits=["200/minute"])


# ---------------------------------------------------------------------------
# Singletons
# ---------------------------------------------------------------------------

nlp_pipeline: Optional[DisasterNLPPipeline] = None
vision_analyzer: Optional[VisionAnalyzer] = None
ensemble_engine: Optional[EnsembleEngine] = None
ingestion_pipeline: Optional[IngestionPipeline] = None
real_ingestion_pipeline: Optional[RealIngestionPipeline] = None
db: Optional[AlertDatabase] = None
notifier: Optional[AlertNotifier] = None


# ---------------------------------------------------------------------------
# Core processing callback
# ---------------------------------------------------------------------------

async def _process_post(post: IngestedPost) -> None:
    """
    Full pipeline:
      IngestedPost → NLP → Vision → Ensemble → DB → Notify
    Runs ML inference in thread executor to avoid blocking the event loop.
    """
    loop = asyncio.get_running_loop()

    # NLP (CPU-bound → executor)
    nlp_result = await loop.run_in_executor(None, nlp_pipeline.analyze, post.text)

    # Vision (only if image URL provided); pass text for cross-modal boosting
    vision_result = None
    if post.image_url:
        vision_result = await loop.run_in_executor(
            None, vision_analyzer.analyze_url, post.image_url, post.text
        )

    # Ensemble
    inp = EnsembleInput(
        nlp_is_disaster=nlp_result.is_disaster_related,
        nlp_disaster_type=nlp_result.disaster_type,
        nlp_confidence=nlp_result.classification_confidence,
        nlp_urgency_score=nlp_result.urgency_score,
        nlp_locations=nlp_result.locations,
        nlp_organizations=nlp_result.organizations,
        nlp_keywords=nlp_result.keywords_found,
        raw_text=post.text,
        vision_is_disaster=vision_result.is_disaster_related if vision_result else False,
        vision_disaster_type=vision_result.disaster_type if vision_result else "none",
        vision_confidence=vision_result.confidence if vision_result else 0.0,
        image_source=post.image_url,
        source_platform=post.platform,
        post_id=post.post_id,
        author=post.author,
    )
    alert = ensemble_engine.process(inp)

    # Persist
    await db.save_alert(alert)

    # Track disaster count in pipeline status
    if alert.severity != AlertSeverity.NONE:
        ingestion_pipeline.status.disasters_detected += 1

    # Notify SSE + webhooks
    alert_dict = {
        "alert_id": alert.alert_id,
        "severity": alert.severity.value,
        "combined_confidence": alert.combined_confidence,
        "disaster_type": alert.disaster_type,
        "locations": alert.locations,
        "organizations": alert.organizations,
        "keywords": alert.keywords,
        "recommended_actions": alert.recommended_actions,
        "source_platform": alert.source_platform,
        "post_id": alert.post_id,
        "author": alert.author,
        "nlp_confidence": alert.nlp_confidence,
        "vision_confidence": alert.vision_confidence,
        "created_at": alert.created_at,
        "insights": alert.insights,
    }
    await notifier.notify(alert_dict)

    logger.info(
        "Processed | severity=%s type=%s platform=%s",
        alert.severity.value, alert.disaster_type, post.platform,
    )


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    global nlp_pipeline, vision_analyzer, ensemble_engine
    global ingestion_pipeline, real_ingestion_pipeline, db, notifier

    logger.info("Starting up — loading AI models...")
    nlp_pipeline = DisasterNLPPipeline()
    vision_analyzer = VisionAnalyzer()
    ensemble_engine = EnsembleEngine()

    db = AlertDatabase()
    await db.init()

    notifier = AlertNotifier()
    await notifier.start()

    ingestion_pipeline = IngestionPipeline(IngestionConfig())
    ingestion_pipeline.set_callback(_process_post)

    real_ingestion_pipeline = RealIngestionPipeline()
    real_ingestion_pipeline.set_callback(_process_post)
    await real_ingestion_pipeline.start()

    logger.info("All systems ready.")
    yield

    logger.info("Shutting down...")
    await ingestion_pipeline.stop()
    await real_ingestion_pipeline.stop()
    await notifier.stop()


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Disaster Alert API",
    description="Real-time social media analysis for disaster detection and emergency alert generation.",
    version="2.0.0",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class PostRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)
    image_url: Optional[str] = None
    source_platform: str = "unknown"
    post_id: Optional[str] = None
    author: Optional[str] = None

    model_config = {"json_schema_extra": {"example": {
        "text": "URGENT: Flooding in Houston TX. Families trapped. Need rescue NOW! #HoustonFlood",
        "source_platform": "twitter",
        "author": "@houstonresident",
    }}}


class BatchRequest(BaseModel):
    posts: list[PostRequest] = Field(..., min_length=1, max_length=50)


class AlertOut(BaseModel):
    alert_id: str
    severity: str
    combined_confidence: float
    disaster_type: str
    locations: list[str]
    organizations: list[str]
    keywords: list[str]
    recommended_actions: list[str]
    insights: dict
    source_platform: str
    post_id: Optional[str]
    author: Optional[str]
    image_source: Optional[str] = None
    nlp_confidence: float
    vision_confidence: float
    created_at: str


class BatchAlertOut(BaseModel):
    total: int
    alerts: list[AlertOut]


class StatsOut(BaseModel):
    total_alerts: int
    by_severity: dict[str, int]
    by_disaster_type: dict[str, int]


class IngestionStatusOut(BaseModel):
    is_running: bool
    posts_processed: int
    disasters_detected: int
    started_at: Optional[str]
    stopped_at: Optional[str]


class WebhookIn(BaseModel):
    url: str = Field(..., description="HTTPS URL to receive POST on each alert")
    secret: Optional[str] = Field(None, description="Optional secret sent as X-Webhook-Secret header")


class WebhookOut(BaseModel):
    id: str
    url: str
    enabled: bool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _guard() -> None:
    """Raise 503 if models not yet loaded."""
    if nlp_pipeline is None or ensemble_engine is None:
        raise HTTPException(503, "Models not yet loaded.")


async def _analyze_request(req: PostRequest) -> AlertOut:
    """Run full pipeline for a single request and return AlertOut."""
    post = IngestedPost(
        text=req.text,
        platform=req.source_platform,
        author=req.author,
        post_id=req.post_id,
        image_url=req.image_url,
    )
    await _process_post(post)
    # Retrieve the just-saved alert (most recent)
    alerts = await db.get_alerts(min_severity="NONE", limit=1, offset=0)
    # Fallback: pull from ensemble in-memory store
    if not alerts:
        a = ensemble_engine._alert_store[-1]
        return _alert_obj_to_out(a)
    return AlertOut(**_db_row_to_out(alerts[0]))


def _db_row_to_out(row: dict) -> dict:
    """Map DB row keys to AlertOut fields."""
    return {
        "alert_id": row["alert_id"],
        "severity": row["severity"],
        "combined_confidence": row["confidence"],
        "disaster_type": row["disaster_type"],
        "locations": row.get("locations") or [],
        "organizations": row.get("organizations") or [],
        "keywords": row.get("keywords") or [],
        "recommended_actions": row.get("actions") or [],
        "insights": row.get("insights") or {},
        "source_platform": row.get("platform", "unknown"),
        "post_id": row.get("post_id"),
        "author": row.get("author"),
        "image_source": row.get("image_source"),
        "nlp_confidence": row.get("nlp_conf", 0.0),
        "vision_confidence": row.get("vision_conf", 0.0),
        "created_at": row["created_at"],
    }


def _alert_obj_to_out(a) -> AlertOut:
    return AlertOut(
        alert_id=a.alert_id,
        severity=a.severity.value,
        combined_confidence=a.combined_confidence,
        disaster_type=a.disaster_type,
        locations=a.locations,
        organizations=a.organizations,
        keywords=a.keywords,
        recommended_actions=a.recommended_actions,
        insights=a.insights,
        source_platform=a.source_platform,
        post_id=a.post_id,
        author=a.author,
        image_source=a.image_source,
        nlp_confidence=a.nlp_confidence,
        vision_confidence=a.vision_confidence,
        created_at=a.created_at,
    )


# ---------------------------------------------------------------------------
# Dashboard  (Gap 1 — serve the HTML dashboard)
# ---------------------------------------------------------------------------

@app.get("/dashboard", tags=["System"], include_in_schema=False)
async def serve_dashboard():
    """Open the live alert dashboard in your browser."""
    dashboard_path = Path(__file__).parent.parent / "dashboard.html"
    if not dashboard_path.exists():
        raise HTTPException(404, "dashboard.html not found")
    return FileResponse(str(dashboard_path), media_type="text/html")


# ---------------------------------------------------------------------------
# System
# ---------------------------------------------------------------------------

@app.get("/health", tags=["System"])
async def health():
    return {
        "status": "ok",
        "models": {
            "nlp": nlp_pipeline is not None,
            "vision": vision_analyzer is not None,
            "ensemble": ensemble_engine is not None,
        },
        "ingestion_running": ingestion_pipeline.status.is_running if ingestion_pipeline else False,
        "sse_subscribers": notifier.sse.subscriber_count if notifier else 0,
    }


# ---------------------------------------------------------------------------
# Analysis (on-demand)
# ---------------------------------------------------------------------------

@app.post("/analyze", response_model=AlertOut, tags=["Analysis"])
@limiter.limit("30/minute")
async def analyze_post(req: PostRequest, request: Request):
    """Analyze a single social media post and return an alert. Rate limited to 30/min per IP."""
    _guard()
    return await _analyze_request(req)


@app.post("/analyze/batch", response_model=BatchAlertOut, tags=["Analysis"])
async def analyze_batch(req: BatchRequest):
    """Analyze up to 50 posts in one call."""
    _guard()
    results = []
    for post_req in req.posts:
        results.append(await _analyze_request(post_req))
    return BatchAlertOut(total=len(results), alerts=results)


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------

@app.get("/ingestion/real/status", tags=["Ingestion"])
async def real_ingestion_status():
    """Status of the real news/GDACS ingestion pipeline."""
    s = real_ingestion_pipeline.status
    return {
        "is_running": s.is_running,
        "posts_processed": s.posts_processed,
        "disasters_detected": s.disasters_detected,
        "started_at": s.started_at,
        "sources": s.sources,
    }


@app.post("/ingestion/start", tags=["Ingestion"])
async def ingestion_start(interval_seconds: float = Query(6.0, ge=1.0, le=60.0)):
    """Start the mock social-media stream."""
    _guard()
    ingestion_pipeline.config.interval_seconds = interval_seconds
    ingestion_pipeline._stream = __import__("data_ingestion").MockSocialMediaStream(
        ingestion_pipeline.config
    )
    started = await ingestion_pipeline.start()
    if not started:
        return {"message": "Already running.", "status": ingestion_pipeline.status}
    return {"message": "Ingestion stream started.", "interval_seconds": interval_seconds}


@app.post("/ingestion/stop", tags=["Ingestion"])
async def ingestion_stop():
    """Stop the mock social-media stream."""
    await ingestion_pipeline.stop()
    return {"message": "Ingestion stream stopped.", "status": ingestion_pipeline.status}


@app.get("/ingestion/status", response_model=IngestionStatusOut, tags=["Ingestion"])
async def ingestion_status():
    """Current ingestion pipeline state and counters."""
    s = ingestion_pipeline.status
    return IngestionStatusOut(
        is_running=s.is_running,
        posts_processed=s.posts_processed,
        disasters_detected=s.disasters_detected,
        started_at=s.started_at,
        stopped_at=s.stopped_at,
    )


@app.post("/ingest", response_model=AlertOut, tags=["Ingestion"])
async def ingest_one(req: PostRequest):
    """Manually submit one post — analyzed immediately and stored."""
    _guard()
    return await _analyze_request(req)


@app.post("/ingest/file", tags=["Ingestion"])
async def ingest_file(file: UploadFile = File(...)):
    """
    Upload a JSON or CSV file of posts for batch ingestion.

    JSON format: [{"text":"...", "platform":"twitter"}, ...]
    CSV columns: text, platform, author, post_id, image_url
    """
    _guard()
    content = await file.read()
    filename = file.filename or ""
    if filename.endswith(".json"):
        posts = FileIngestionService.from_json(content)
    elif filename.endswith(".csv"):
        posts = FileIngestionService.from_csv(content)
    else:
        raise HTTPException(400, "Only .json and .csv files are supported.")

    if not posts:
        raise HTTPException(422, "No valid posts found in the file.")

    count = await ingestion_pipeline.ingest_posts(posts)
    return {"message": f"Ingested {count} posts from '{filename}'.", "count": count}


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------

@app.get("/alerts", response_model=BatchAlertOut, tags=["Alerts"])
async def list_alerts(
    min_severity: str = Query("LOW", description="NONE | LOW | MEDIUM | HIGH | CRITICAL"),
    disaster_type: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """List stored alerts with optional severity/type filters and pagination."""
    rows = await db.get_alerts(
        min_severity=min_severity.upper(),
        limit=limit,
        offset=offset,
        disaster_type=disaster_type,
    )
    alerts = [AlertOut(**_db_row_to_out(r)) for r in rows]
    return BatchAlertOut(total=len(alerts), alerts=alerts)


@app.get("/alerts/stats", response_model=StatsOut, tags=["Alerts"])
async def alert_stats():
    """Aggregate counts by severity and disaster type."""
    return StatsOut(**(await db.stats()))


@app.delete("/alerts", tags=["Alerts"])
async def clear_alerts():
    """Delete all stored alerts (useful for demo resets)."""
    count = await db.clear()
    ensemble_engine.clear_alerts()
    return {"message": f"Cleared {count} alerts."}


@app.get("/alerts/{alert_id}", response_model=AlertOut, tags=["Alerts"])
async def get_alert(alert_id: str):
    """Fetch a specific alert by ID."""
    row = await db.get_by_id(alert_id)
    if not row:
        raise HTTPException(404, f"Alert '{alert_id}' not found.")
    return AlertOut(**_db_row_to_out(row))


# ---------------------------------------------------------------------------
# Real-time SSE stream
# ---------------------------------------------------------------------------

@app.get("/stream/alerts", tags=["Real-time"])
async def stream_alerts(request: Request):
    """
    Server-Sent Events stream.
    Connect once — receives every new alert in real-time as JSON.

    Example (browser):
        const es = new EventSource('/stream/alerts');
        es.onmessage = e => console.log(JSON.parse(e.data));
    """
    queue = notifier.sse.subscribe()

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=25.0)
                    yield f"data: {json.dumps(payload, default=str)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"   # prevent connection timeout
        finally:
            notifier.sse.unsubscribe(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------

@app.get("/webhooks", tags=["Webhooks"])
async def list_webhooks():
    whs = notifier.list_webhooks()
    return {"total": len(whs), "webhooks": [WebhookOut(id=w.id, url=w.url, enabled=w.enabled) for w in whs]}


@app.post("/webhooks", tags=["Webhooks"])
async def add_webhook(body: WebhookIn):
    """Register a URL to receive HTTP POST on every new alert."""
    wh = notifier.add_webhook(body.url, body.secret)
    return WebhookOut(id=wh.id, url=wh.url, enabled=wh.enabled)


@app.delete("/webhooks/{webhook_id}", tags=["Webhooks"])
async def remove_webhook(webhook_id: str):
    removed = notifier.remove_webhook(webhook_id)
    if not removed:
        raise HTTPException(404, f"Webhook '{webhook_id}' not found.")
    return {"message": f"Webhook '{webhook_id}' removed."}


# ---------------------------------------------------------------------------
# Demo  (Gap 7 — uses headline.json)
# ---------------------------------------------------------------------------

@app.post("/demo", tags=["System"])
async def run_demo(background_tasks: BackgroundTasks):
    """
    Feed all posts from headline.json through the pipeline with a 1-second delay.
    Open the dashboard and hit this endpoint to see live alerts populate.
    """
    import json as _json
    from pathlib import Path

    headline_path = Path(__file__).parent.parent / "headline.json"
    if not headline_path.exists():
        raise HTTPException(404, "headline.json not found")

    posts_raw = _json.loads(headline_path.read_text())

    async def _run():
        for item in posts_raw:
            post = IngestedPost(
                text=item.get("text", ""),
                platform=item.get("platform", "demo"),
                author=item.get("author"),
            )
            await _process_post(post)
            await asyncio.sleep(1.0)

    background_tasks.add_task(_run)
    return {"message": f"Demo started — streaming {len(posts_raw)} posts with 1s delay."}


# ---------------------------------------------------------------------------
# Real ingestion stream control  (Gap 4)
# ---------------------------------------------------------------------------

@app.post("/stream/start", tags=["Ingestion"])
async def stream_start():
    """Start the real social-media / news ingestion pipeline."""
    started = await real_ingestion_pipeline.start()
    if not started:
        return {"message": "Real ingestion already running.", "status": real_ingestion_pipeline.status.is_running}
    return {"message": "Real ingestion pipeline started.", "sources": list(real_ingestion_pipeline.status.sources.keys())}


@app.post("/stream/stop", tags=["Ingestion"])
async def stream_stop():
    """Stop the real social-media / news ingestion pipeline."""
    await real_ingestion_pipeline.stop()
    return {"message": "Real ingestion pipeline stopped.", "posts_processed": real_ingestion_pipeline.status.posts_processed}


# ---------------------------------------------------------------------------
# Trends  (Gap 10)
# ---------------------------------------------------------------------------

@app.get("/trends", tags=["Trends"])
async def get_trends():
    """Active disaster trend clusters detected in the rolling 10-minute window."""
    trends = ensemble_engine.trend_engine.get_active_trends()
    stats  = ensemble_engine.trend_engine.get_stats()
    return {
        "stats": stats,
        "trends": [
            {
                "trend_id":             t.trend_id,
                "disaster_type":        t.disaster_type,
                "location_name":        t.location_name,
                "signal_count":         t.signal_count,
                "avg_confidence":       t.avg_confidence,
                "severity":             t.severity,
                "first_seen":           t.first_seen,
                "last_seen":            t.last_seen,
                "is_new":               t.is_new,
                "is_escalated":         t.is_escalated,
                "representative_texts": t.representative_texts,
            }
            for t in trends
        ],
    }


# ---------------------------------------------------------------------------
# Model training trigger  (Gap 5)
# ---------------------------------------------------------------------------

@app.post("/train", tags=["System"])
async def trigger_training(background_tasks):
    """
    Kick off BERTweet fine-tuning in the background.
    After completion, the new model is hot-reloaded without restarting the server.
    Training takes ~5-10 min on GPU, longer on CPU.
    """
    from nlp.nlp_pipeline import reload_model
    import subprocess, sys

    def _run_training():
        try:
            subprocess.run(
                [sys.executable, "-m", "nlp.modeltrainer"],
                check=True,
                timeout=1800,   # 30-min hard cap
            )
            reload_model()
            logger.info("Training complete and model reloaded.")
        except Exception as exc:
            logger.error("Training failed: %s", exc)

    background_tasks.add_task(_run_training)
    return {"message": "Training started in background. Model will hot-reload on completion."}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run("nlp.api:app", host="0.0.0.0", port=8000, reload=True)
