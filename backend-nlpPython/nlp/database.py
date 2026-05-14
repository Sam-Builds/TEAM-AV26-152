"""
SQLite Alert Database
---------------------
Persists alerts across server restarts.

Tables:
  alerts            — every alert ever generated
  ingestion_sessions— log of ingestion run stats

All public methods are async (SQLite ops run in a thread executor).
"""

import asyncio
import functools
import json
import logging
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from nlp.models import Alert

logger = logging.getLogger(__name__)

DB_PATH = Path("alerts.db")
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="sqlite")

# Severity rank for filtering
_SEVERITY_RANK = {"NONE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}


# ---------------------------------------------------------------------------
# Sync primitives (run inside executor)
# ---------------------------------------------------------------------------

def _init_db() -> None:
    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS alerts (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                alert_id           TEXT    UNIQUE NOT NULL,
                severity           TEXT    NOT NULL,
                confidence         REAL,
                disaster_type      TEXT,
                locations          TEXT,       -- JSON array
                organizations      TEXT,       -- JSON array
                keywords           TEXT,       -- JSON array
                raw_text           TEXT,
                actions            TEXT,       -- JSON array
                insights           TEXT,       -- JSON object
                platform           TEXT,
                post_id            TEXT,
                author             TEXT,
                image_source       TEXT,
                nlp_conf           REAL,
                vision_conf        REAL,
                created_at         TEXT,
                geocoded_locations TEXT,       -- JSON array of {name,lat,lng,address}
                is_duplicate       INTEGER DEFAULT 0,
                trend_id           TEXT
            );

            CREATE TABLE IF NOT EXISTS ingestion_sessions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                source          TEXT,
                posts_processed INTEGER DEFAULT 0,
                disasters_found INTEGER DEFAULT 0,
                started_at      TEXT,
                ended_at        TEXT,
                status          TEXT DEFAULT 'running'
            );
        """)
        # ── Migration: add new columns to existing DBs ───────────────
        _add_column_if_missing(conn, "alerts", "geocoded_locations", "TEXT")
        _add_column_if_missing(conn, "alerts", "is_duplicate",       "INTEGER DEFAULT 0")
        _add_column_if_missing(conn, "alerts", "trend_id",           "TEXT")
    logger.info("SQLite DB ready at %s", DB_PATH)


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, col_def: str) -> None:
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")
        logger.info("DB migration: added column '%s' to '%s'", column, table)


def _save_alert(alert: Alert) -> None:
    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.execute("""
            INSERT OR IGNORE INTO alerts
              (alert_id, severity, confidence, disaster_type,
               locations, organizations, keywords, raw_text,
               actions, insights, platform, post_id,
               author, image_source, nlp_conf, vision_conf, created_at,
               geocoded_locations, is_duplicate, trend_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            alert.alert_id,
            alert.severity.value,
            alert.combined_confidence,
            alert.disaster_type,
            json.dumps(alert.locations),
            json.dumps(alert.organizations),
            json.dumps(alert.keywords),
            alert.raw_text,
            json.dumps(alert.recommended_actions),
            json.dumps(alert.insights),
            alert.source_platform,
            alert.post_id,
            alert.author,
            alert.image_source,
            alert.nlp_confidence,
            alert.vision_confidence,
            alert.created_at,
            json.dumps(alert.geocoded_locations),
            1 if alert.is_duplicate else 0,
            alert.trend_id,
        ))


def _get_alerts(
    min_severity: str,
    limit: int,
    offset: int,
    disaster_type: Optional[str],
) -> list[dict]:
    min_rank = _SEVERITY_RANK.get(min_severity, 1)
    allowed = [s for s, r in _SEVERITY_RANK.items() if r >= min_rank and s != "NONE"]
    placeholders = ",".join("?" * len(allowed))
    query = f"SELECT * FROM alerts WHERE severity IN ({placeholders})"
    params: list = list(allowed)
    if disaster_type:
        query += " AND disaster_type = ?"
        params.append(disaster_type)
    query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(query, params).fetchall()
    return [_row_to_dict(r) for r in rows]


def _get_by_id(alert_id: str) -> Optional[dict]:
    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM alerts WHERE alert_id = ?", (alert_id,)
        ).fetchone()
    return _row_to_dict(row) if row else None


def _get_stats() -> dict:
    with sqlite3.connect(str(DB_PATH)) as conn:
        total = conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
        by_sev = {
            row[0]: row[1]
            for row in conn.execute("SELECT severity, COUNT(*) FROM alerts GROUP BY severity")
        }
        by_type = {
            row[0]: row[1]
            for row in conn.execute("SELECT disaster_type, COUNT(*) FROM alerts GROUP BY disaster_type")
        }
    return {"total_alerts": total, "by_severity": by_sev, "by_disaster_type": by_type}


def _clear_alerts() -> int:
    with sqlite3.connect(str(DB_PATH)) as conn:
        count = conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
        conn.execute("DELETE FROM alerts")
    return count


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    for col in ("locations", "organizations", "keywords", "actions", "insights",
                "geocoded_locations"):
        if d.get(col):
            try:
                d[col] = json.loads(d[col])
            except Exception:
                pass
    # Normalise booleans
    d["is_duplicate"] = bool(d.get("is_duplicate", 0))
    return d


# ---------------------------------------------------------------------------
# Async public class
# ---------------------------------------------------------------------------

class AlertDatabase:
    """Async wrapper around synchronous SQLite operations."""

    def __init__(self) -> None:
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    async def init(self) -> None:
        self._loop = asyncio.get_running_loop()
        await self._loop.run_in_executor(_executor, _init_db)

    async def save_alert(self, alert: Alert) -> None:
        await self._loop.run_in_executor(_executor, _save_alert, alert)

    async def get_alerts(
        self,
        min_severity: str = "LOW",
        limit: int = 50,
        offset: int = 0,
        disaster_type: Optional[str] = None,
    ) -> list[dict]:
        fn = functools.partial(_get_alerts, min_severity, limit, offset, disaster_type)
        return await self._loop.run_in_executor(_executor, fn)

    async def get_by_id(self, alert_id: str) -> Optional[dict]:
        return await self._loop.run_in_executor(_executor, _get_by_id, alert_id)

    async def stats(self) -> dict:
        return await self._loop.run_in_executor(_executor, _get_stats)

    async def clear(self) -> int:
        return await self._loop.run_in_executor(_executor, _clear_alerts)
