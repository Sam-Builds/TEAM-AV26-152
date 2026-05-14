"""
Shared data models for the Disaster Alert System.
Imported by nlp_pipeline, vision_analyzer, ensemble_engine, database, and api.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


# ─────────────────────────────────────────────────────────────────────────────
# SEVERITY LEVELS
# ─────────────────────────────────────────────────────────────────────────────

class AlertSeverity(str, Enum):
    NONE     = "NONE"
    LOW      = "LOW"
    MEDIUM   = "MEDIUM"
    HIGH     = "HIGH"
    CRITICAL = "CRITICAL"


# ─────────────────────────────────────────────────────────────────────────────
# NLP PIPELINE RESULT
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class NLPResult:
    is_disaster_related:       bool
    disaster_type:             str          # e.g. "flood", "earthquake", "none"
    classification_confidence: float        # 0.0 – 1.0
    urgency_score:             float        # 0.0 – 1.0
    locations:                 list[str]
    organizations:             list[str]
    keywords_found:            list[str]
    geocoded_locations:        list[dict] = field(default_factory=list)
    # Each dict: {"name": str, "lat": float, "lng": float, "address": str, "ner_confidence": float}


# ─────────────────────────────────────────────────────────────────────────────
# VISION ANALYZER RESULT
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class VisionResult:
    is_disaster_related: bool
    disaster_type:       str    # best-guess category or "none"
    confidence:          float  # 0.0 – 1.0


# ─────────────────────────────────────────────────────────────────────────────
# ENSEMBLE INPUT  (feeds into EnsembleEngine.process)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class EnsembleInput:
    # NLP signals
    nlp_is_disaster:          bool
    nlp_disaster_type:        str
    nlp_confidence:           float
    nlp_urgency_score:        float
    nlp_locations:            list[str]
    nlp_organizations:        list[str]
    nlp_keywords:             list[str]
    raw_text:                 str

    # Geocoded location data from NLP (lat/lng dicts)
    nlp_geocoded_locations:   list[dict] = field(default_factory=list)

    # Vision signals (optional)
    vision_is_disaster:   bool          = False
    vision_disaster_type: str           = "none"
    vision_confidence:    float         = 0.0

    # Metadata
    image_source:     Optional[str] = None
    source_platform:  str           = "unknown"
    post_id:          Optional[str] = None
    author:           Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
# ALERT  (output of EnsembleEngine, persisted by AlertDatabase)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Alert:
    alert_id:            str
    severity:            AlertSeverity
    combined_confidence: float
    disaster_type:       str
    locations:           list[str]
    organizations:       list[str]
    keywords:            list[str]
    recommended_actions: list[str]
    insights:            dict
    source_platform:     str
    post_id:             Optional[str]
    author:              Optional[str]
    image_source:        Optional[str]
    nlp_confidence:      float
    vision_confidence:   float
    created_at:          str
    raw_text:            str = ""

    # Location intelligence — lat/lng for every extracted location
    geocoded_locations:  list[dict] = field(default_factory=list)

    # Deduplication flag — True means a cooldown is active for this location/type;
    # the alert is stored for analytics but SSE/webhook notification is suppressed.
    is_duplicate:        bool = False

    # Trend association — set when this alert contributes to a detected trend
    trend_id:            Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
# TREND ALERT  (emitted by TrendEngine when a cluster threshold is crossed)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TrendAlert:
    """
    Fired when corroborating disaster signals from the same area exceed a
    threshold within the rolling time window.

    Severity levels:
      WATCH    — 3+ signals in 10 min  (possible incident forming)
      ADVISORY — 5+ signals in 10 min  (confirmed incident, monitor closely)
      WARNING  — 10+ signals in 10 min (major incident, immediate action)
    """
    trend_id:             str
    disaster_type:        str
    location_key:         str     # normalised location identifier
    location_name:        str     # human-readable location name
    signal_count:         int
    avg_confidence:       float
    severity:             str     # "WATCH" | "ADVISORY" | "WARNING"
    first_seen:           str     # ISO-8601
    last_seen:            str     # ISO-8601
    window_minutes:       int = 10
    representative_texts: list[str] = field(default_factory=list)
    is_new:               bool = False      # True when cluster first crosses a threshold
    is_escalated:         bool = False      # True when severity level increases
