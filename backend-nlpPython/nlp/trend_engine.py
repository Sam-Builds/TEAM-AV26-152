"""
Trend & Pattern Detection Engine
----------------------------------
Detects emerging disaster patterns by tracking clusters of signals (individual
disaster-classified posts) in rolling time windows per (disaster_type, location).

Thresholds
----------
  WATCH    : 3+ signals in 10 min  — possible incident forming
  ADVISORY : 5+ signals in 10 min  — confirmed incident, escalate response
  WARNING  : 10+ signals in 10 min — major incident, immediate action needed

add_signal() is the only write method.  It returns a TrendAlert when:
  • a cluster first crosses a threshold (is_new=True), OR
  • an existing trend's severity level increases (is_escalated=True).
Returning None for routine accumulation avoids notification spam.
"""

from __future__ import annotations

import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Optional

from nlp.models import TrendAlert

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

TREND_WINDOW_SECONDS = 600    # 10-minute rolling window
WATCH_THRESHOLD      = 3
ADVISORY_THRESHOLD   = 5
WARNING_THRESHOLD    = 10
MIN_CONFIDENCE       = 0.30   # ignore low-confidence NLP results


# ─────────────────────────────────────────────────────────────────────────────
# Internal signal record
# ─────────────────────────────────────────────────────────────────────────────

class _Signal:
    __slots__ = ("timestamp", "confidence", "location_name", "post_id", "raw_text")

    def __init__(
        self,
        confidence:    float,
        location_name: str,
        post_id:       Optional[str] = None,
        raw_text:      str = "",
    ) -> None:
        self.timestamp     = time.monotonic()
        self.confidence    = confidence
        self.location_name = location_name
        self.post_id       = post_id
        self.raw_text      = raw_text[:200]


# ─────────────────────────────────────────────────────────────────────────────
# TrendEngine
# ─────────────────────────────────────────────────────────────────────────────

class TrendEngine:
    """
    Stateful singleton — one instance per server process (owned by EnsembleEngine).

    Thread-safety note: all writes come from a single asyncio event loop via
    executor callbacks; no explicit locking is needed for CPython.
    """

    def __init__(self) -> None:
        # (disaster_type, location_key) → deque[_Signal]
        self._signals: dict[tuple, deque] = defaultdict(deque)
        # trend_id → TrendAlert
        self._trends:  dict[str, TrendAlert] = {}
        # (disaster_type, location_key) → trend_id
        self._key_to_tid: dict[tuple, str] = {}

    # ------------------------------------------------------------------
    # Public write
    # ------------------------------------------------------------------

    def add_signal(
        self,
        disaster_type:  str,
        location_key:   str,
        location_name:  str,
        confidence:     float,
        post_id:        Optional[str] = None,
        raw_text:       str = "",
    ) -> Optional[TrendAlert]:
        """
        Register one disaster signal.

        Returns a TrendAlert when:
          • the cluster first crosses a severity threshold (is_new=True)
          • the cluster escalates to a higher severity level (is_escalated=True)
        Returns None for every other accumulation step.
        """
        if confidence < MIN_CONFIDENCE:
            return None

        key = (disaster_type, location_key)
        self._evict(key)

        sig = _Signal(
            confidence=confidence,
            location_name=location_name,
            post_id=post_id,
            raw_text=raw_text,
        )
        self._signals[key].append(sig)

        count   = len(self._signals[key])
        new_sev = _severity_for_count(count)
        if new_sev is None:
            return None

        all_sigs  = list(self._signals[key])
        avg_conf  = sum(s.confidence for s in all_sigs) / len(all_sigs)
        rep_texts = [s.raw_text for s in all_sigs if s.raw_text][-3:]
        now_iso   = datetime.now(timezone.utc).isoformat()
        first_iso = _monotonic_to_iso(all_sigs[0].timestamp)

        if key in self._key_to_tid:
            # ── Update existing trend ──────────────────────────────────
            tid      = self._key_to_tid[key]
            existing = self._trends[tid]
            is_esc   = new_sev != existing.severity

            updated = TrendAlert(
                trend_id=tid,
                disaster_type=disaster_type,
                location_key=location_key,
                location_name=location_name or existing.location_name,
                signal_count=count,
                avg_confidence=round(avg_conf, 3),
                severity=new_sev,
                first_seen=existing.first_seen,
                last_seen=now_iso,
                representative_texts=rep_texts,
                is_new=False,
                is_escalated=is_esc,
            )
            self._trends[tid] = updated
            return updated if is_esc else None   # only surface escalations

        else:
            # ── New trend ──────────────────────────────────────────────
            tid = uuid.uuid4().hex[:10]
            self._key_to_tid[key] = tid
            trend = TrendAlert(
                trend_id=tid,
                disaster_type=disaster_type,
                location_key=location_key,
                location_name=location_name or "unknown",
                signal_count=count,
                avg_confidence=round(avg_conf, 3),
                severity=new_sev,
                first_seen=first_iso,
                last_seen=now_iso,
                representative_texts=rep_texts,
                is_new=True,
                is_escalated=False,
            )
            self._trends[tid] = trend
            return trend

    # ------------------------------------------------------------------
    # Public reads
    # ------------------------------------------------------------------

    def get_active_trends(self) -> list[TrendAlert]:
        """Return all trends with live signals, sorted by signal_count desc."""
        self._cleanup()
        return sorted(self._trends.values(), key=lambda t: t.signal_count, reverse=True)

    def get_trend(self, trend_id: str) -> Optional[TrendAlert]:
        return self._trends.get(trend_id)

    def get_stats(self) -> dict:
        self._cleanup()
        trends   = list(self._trends.values())
        by_type: dict[str, int] = {}
        by_sev:  dict[str, int] = {}
        for t in trends:
            by_type[t.disaster_type] = by_type.get(t.disaster_type, 0) + 1
            by_sev[t.severity]       = by_sev.get(t.severity, 0) + 1
        return {
            "active_trends":    len(trends),
            "by_disaster_type": by_type,
            "by_severity":      by_sev,
            "window_minutes":   TREND_WINDOW_SECONDS // 60,
            "thresholds": {
                "WATCH":    WATCH_THRESHOLD,
                "ADVISORY": ADVISORY_THRESHOLD,
                "WARNING":  WARNING_THRESHOLD,
            },
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _evict(self, key: tuple) -> None:
        cutoff = time.monotonic() - TREND_WINDOW_SECONDS
        q = self._signals[key]
        while q and q[0].timestamp < cutoff:
            q.popleft()

    def _cleanup(self) -> None:
        """Remove (type, location) keys whose signal queues have gone empty."""
        stale = []
        for k in list(self._signals.keys()):
            self._evict(k)
            if not self._signals[k]:
                stale.append(k)
        for k in stale:
            del self._signals[k]
            tid = self._key_to_tid.pop(k, None)
            if tid and tid in self._trends:
                del self._trends[tid]


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _severity_for_count(count: int) -> Optional[str]:
    if count >= WARNING_THRESHOLD:  return "WARNING"
    if count >= ADVISORY_THRESHOLD: return "ADVISORY"
    if count >= WATCH_THRESHOLD:    return "WATCH"
    return None


def _monotonic_to_iso(mono: float) -> str:
    """Convert a time.monotonic() value to an approximate UTC ISO-8601 string."""
    offset = time.monotonic() - mono
    ts = datetime.now(timezone.utc).timestamp() - offset
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()
