"""
ENSEMBLE ALERT ENGINE
Fuses: NLP analysis + CLIP vision + temporal clustering + source credibility
This is what makes the system genuinely smart — not just one model, but 4 signals working together.

Key ideas:
- Single high-confidence tweet → MEDIUM alert max
- 3+ tweets from same area in 10 min → escalate to HIGH automatically
- Verified account or media source → 1.5x credibility multiplier
- Image confirms text → composite score boosted
"""

import time
import uuid
import hashlib
from collections import defaultdict, deque, OrderedDict
from datetime import datetime, timezone
from typing import Optional

from nlp.models import Alert, AlertSeverity, EnsembleInput
from nlp.trend_engine import TrendEngine


# ─────────────────────────────────────────────────────────
# TEMPORAL CLUSTERING
# Groups tweets by area + disaster type in a rolling time window
# More tweets = higher confidence the event is real
# ─────────────────────────────────────────────────────────

class TemporalCluster:
    """Sliding window of disaster signals per (location, category) pair."""

    WINDOW_SECONDS = 600   # 10-minute rolling window

    def __init__(self):
        # Key: (location_key, category) → deque of (timestamp, confidence) tuples
        self._clusters: dict = defaultdict(deque)

    def _evict_old(self, key: str):
        cutoff = time.time() - self.WINDOW_SECONDS
        while self._clusters[key] and self._clusters[key][0][0] < cutoff:
            self._clusters[key].popleft()

    def add_signal(self, location_key: str, category: str, confidence: float):
        key = f"{location_key}|{category}"
        self._evict_old(key)
        self._clusters[key].append((time.time(), confidence))

    def get_cluster_stats(self, location_key: str, category: str) -> dict:
        key = f"{location_key}|{category}"
        self._evict_old(key)
        signals = self._clusters[key]

        if not signals:
            return {"count": 0, "avg_confidence": 0.0, "max_confidence": 0.0}

        confidences = [s[1] for s in signals]
        return {
            "count":          len(signals),
            "avg_confidence": round(sum(confidences) / len(confidences), 3),
            "max_confidence": round(max(confidences), 3),
            "first_seen":     datetime.fromtimestamp(signals[0][0]).isoformat(),
            "last_seen":      datetime.fromtimestamp(signals[-1][0]).isoformat(),
        }

    def get_cluster_severity_boost(self, count: int) -> float:
        """More corroborating tweets = higher confidence multiplier."""
        if count >= 10: return 1.5
        if count >= 5:  return 1.3
        if count >= 3:  return 1.15
        return 1.0


# ─────────────────────────────────────────────────────────
# SOURCE CREDIBILITY SCORING
# Verified accounts + media sources get higher trust
# ─────────────────────────────────────────────────────────

TRUSTED_SOURCES = {
    "ndmaindia", "ndrf_india", "bsf_india", "indianredcross",
    "airnewsalerts", "doordarshan", "ndtv", "timesofindia",
    "thehindu", "indianexpress", "ani_newser", "ptiindia",
}

def score_source_credibility(author_handle: str, is_verified: bool, followers: int) -> dict:
    handle_lower = author_handle.lower().lstrip("@")
    score = 0.5   # baseline

    if handle_lower in TRUSTED_SOURCES:
        score = 1.0
    elif is_verified:
        score = 0.85
    elif followers >= 100_000:
        score += 0.25
    elif followers >= 10_000:
        score += 0.15
    elif followers >= 1_000:
        score += 0.05

    return {
        "credibility_score":  round(min(score, 1.0), 2),
        "is_trusted_source":  handle_lower in TRUSTED_SOURCES,
        "is_verified":        is_verified,
        "follower_tier":      (
            "major" if followers >= 100_000 else
            "established" if followers >= 10_000 else
            "regular"
        )
    }


# ─────────────────────────────────────────────────────────
# LLM SITUATION BRIEF GENERATOR
# Uses GPT/Gemini to generate human-readable brief for responders
# Falls back to template if no API key
# ─────────────────────────────────────────────────────────

import os, json
import requests as req

def generate_situation_brief(alert: dict) -> str:
    """Generate a 3-sentence actionable brief for emergency responders."""

    # Template fallback (always works, no API needed)
    loc_names = [g["address"].split(",")[0] for g in alert.get("geocoded_locations", [])]
    location_str = ", ".join(loc_names) if loc_names else "location under extraction"
    category     = alert.get("category", "disaster").replace("_", " ").title()
    severity     = alert.get("final_severity", "MEDIUM")
    urgency_terms = alert.get("urgency", {}).get("matched_terms", [])
    cluster_count = alert.get("cluster_stats", {}).get("count", 1)

    template = (
        f"SITUATION: {category} event detected in {location_str} with {severity} severity. "
        f"Signal corroborated by {cluster_count} post(s) within 10 minutes. "
        f"Key urgency indicators: {', '.join(urgency_terms[:4]) if urgency_terms else 'standard alert'}. "
        f"Recommend immediate verification and pre-positioning of response assets."
    )

    # Try OpenAI if key available
    openai_key = os.environ.get("OPENAI_API_KEY")
    if openai_key:
        try:
            resp = req.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {openai_key}"},
                json={
                    "model": "gpt-4o-mini",
                    "max_tokens": 120,
                    "messages": [{
                        "role": "system",
                        "content": "You are an emergency response briefing system. Generate a 3-sentence situation brief for first responders. Be factual, concise, and action-oriented."
                    }, {
                        "role": "user",
                        "content": f"Generate a situation brief for this disaster alert:\n{json.dumps(alert, indent=2)}"
                    }]
                },
                timeout=8
            )
            return resp.json()["choices"][0]["message"]["content"]
        except:
            pass

    return template


# ─────────────────────────────────────────────────────────
# DEDUPLICATION
# Don't fire multiple alerts for the same event
# ─────────────────────────────────────────────────────────

class AlertDeduplicator:
    COOLDOWN_SECONDS = 300   # 5-minute cooldown per (location, category)

    def __init__(self):
        self._last_alert: dict = {}

    def should_alert(self, location_key: str, category: str) -> bool:
        key = f"{location_key}|{category}"
        last = self._last_alert.get(key, 0)
        if time.time() - last > self.COOLDOWN_SECONDS:
            self._last_alert[key] = time.time()
            return True
        return False


# ─────────────────────────────────────────────────────────
# MASTER ENSEMBLE PROCESSOR
# ─────────────────────────────────────────────────────────

cluster_engine  = TemporalCluster()
deduplicator    = AlertDeduplicator()

# ─────────────────────────────────────────────────────────
# RECOMMENDED ACTIONS  (per disaster type)
# ─────────────────────────────────────────────────────────

RECOMMENDED_ACTIONS: dict[str, list[str]] = {
    "flood": [
        "Evacuate low-lying areas immediately",
        "Move to higher ground",
        "Avoid flooded roads and bridges",
        "Contact local emergency services (112)",
        "Keep emergency kit ready",
    ],
    "earthquake": [
        "Drop, Cover, and Hold On",
        "Evacuate building if structurally safe",
        "Check for gas leaks and fire hazards",
        "Stay away from damaged structures",
        "Be prepared for aftershocks",
    ],
    "fire": [
        "Evacuate immediately — do not delay",
        "Call fire emergency services (101)",
        "Stay low to avoid smoke inhalation",
        "Close doors to slow fire spread",
        "Do not use elevators",
    ],
    "cyclone": [
        "Seek sturdy shelter immediately",
        "Stay away from windows and doors",
        "Follow official evacuation orders",
        "Stock emergency supplies (water, food, medicine)",
        "Avoid coastal areas and water bodies",
    ],
    "infrastructure": [
        "Avoid the affected area immediately",
        "Report to local authorities and NDRF",
        "Follow safety cordons and instructions",
        "Do not attempt self-rescue without training",
    ],
    "medical": [
        "Contact emergency medical services (108)",
        "Administer first aid if trained",
        "Isolate if infectious disease is suspected",
        "Keep area clear for medical responders",
    ],
    "default": [
        "Follow instructions from local authorities",
        "Stay away from affected areas",
        "Keep emergency services informed",
        "Monitor official updates",
    ],
}


# ─────────────────────────────────────────────────────────
# CLASS-BASED ENSEMBLE ENGINE  (used by api.py)
# ─────────────────────────────────────────────────────────

class EnsembleEngine:
    """
    Fuses NLP + Vision results with temporal clustering and source credibility
    into a final Alert object.  One instance is created at server startup.
    """

    def __init__(self) -> None:
        self._cluster      = TemporalCluster()
        self._deduplicator = AlertDeduplicator()
        self._alert_store: list[Alert] = []

        # Content-hash deduplication — prevents the exact same text being processed
        # multiple times (e.g. retweets, cross-platform reposts).
        # OrderedDict preserves insertion order for bounded eviction.
        self._seen_hashes: OrderedDict[str, float] = OrderedDict()   # hash → timestamp

        # Trend / pattern detection engine
        self.trend_engine = TrendEngine()

    # ------------------------------------------------------------------

    def process(self, inp: EnsembleInput) -> Alert:
        """
        Full ensemble processing.  Returns Alert regardless of severity.
        (NONE severity = not disaster-related or duplicate suppressed.)

        Pipeline:
          0. Content-hash deduplication   — exact same text already seen?
          1. Non-disaster fast path       — NLP says not a disaster
          2. Temporal clustering          — corroborating signal count
          3. Composite scoring            — NLP + vision + urgency + cluster
          4. Severity mapping             — score → NONE/LOW/MEDIUM/HIGH/CRITICAL
          5. Cooldown deduplication       — same (location, type) within 5 min?
          6. Trend signal                 — update TrendEngine, attach trend_id
          7. Build & store Alert
        """
        alert_id   = uuid.uuid4().hex[:12]
        created_at = datetime.now(timezone.utc).isoformat()

        # ── 0. Content-hash deduplication ────────────────────────────
        if inp.raw_text:
            text_hash = hashlib.md5(inp.raw_text.strip().lower().encode()).hexdigest()
            if text_hash in self._seen_hashes:
                alert = Alert(
                    alert_id=alert_id,
                    severity=AlertSeverity.NONE,
                    combined_confidence=inp.nlp_confidence,
                    disaster_type="none",
                    locations=[],
                    organizations=[],
                    keywords=[],
                    recommended_actions=[],
                    insights={"reason": "duplicate_content: identical text already processed"},
                    source_platform=inp.source_platform,
                    post_id=inp.post_id,
                    author=inp.author,
                    image_source=inp.image_source,
                    nlp_confidence=inp.nlp_confidence,
                    vision_confidence=0.0,
                    created_at=created_at,
                    raw_text=inp.raw_text,
                    is_duplicate=True,
                )
                self._alert_store.append(alert)
                return alert
            # Register hash (bounded at 5 000 entries)
            if len(self._seen_hashes) >= 5_000:
                for _ in range(1_000):
                    self._seen_hashes.popitem(last=False)
            self._seen_hashes[text_hash] = time.time()

        # ── 1. Non-disaster fast path ─────────────────────────────────
        if not inp.nlp_is_disaster:
            alert = Alert(
                alert_id=alert_id,
                severity=AlertSeverity.NONE,
                combined_confidence=inp.nlp_confidence,
                disaster_type="none",
                locations=[],
                organizations=[],
                keywords=[],
                recommended_actions=[],
                insights={"reason": "NLP: not disaster-related"},
                source_platform=inp.source_platform,
                post_id=inp.post_id,
                author=inp.author,
                image_source=inp.image_source,
                nlp_confidence=inp.nlp_confidence,
                vision_confidence=0.0,
                created_at=created_at,
                raw_text=inp.raw_text,
                geocoded_locations=inp.nlp_geocoded_locations,
            )
            self._alert_store.append(alert)
            return alert

        # ── 2. Temporal clustering ────────────────────────────────────
        location_key = (
            inp.nlp_locations[0].lower().replace(" ", "_")
            if inp.nlp_locations
            else "unknown"
        )
        self._cluster.add_signal(location_key, inp.nlp_disaster_type, inp.nlp_confidence)
        cluster_stats = self._cluster.get_cluster_stats(location_key, inp.nlp_disaster_type)
        cluster_mult  = self._cluster.get_cluster_severity_boost(cluster_stats["count"])

        # ── 3. Composite score ────────────────────────────────────────
        if inp.vision_is_disaster:
            base = inp.nlp_confidence * 0.6 + inp.vision_confidence * 0.4
        else:
            base = inp.nlp_confidence

        base        = base * (1.0 + inp.nlp_urgency_score * 0.30)   # urgency ≤+30 %
        final_score = round(min(base * cluster_mult, 1.0), 4)

        # ── 4. Severity mapping ───────────────────────────────────────
        if final_score >= 0.75:
            severity = AlertSeverity.CRITICAL
        elif final_score >= 0.55:
            severity = AlertSeverity.HIGH
        elif final_score >= 0.35:
            severity = AlertSeverity.MEDIUM
        elif final_score >= 0.15:
            severity = AlertSeverity.LOW
        else:
            severity = AlertSeverity.NONE

        # ── 5. Cooldown deduplication ─────────────────────────────────
        # If the same (location, disaster_type) already fired an alert within
        # COOLDOWN_SECONDS, mark as duplicate so api.py suppresses notification.
        # The alert is still saved for analytics / trend counting.
        is_duplicate = (
            severity != AlertSeverity.NONE
            and not self._deduplicator.should_alert(location_key, inp.nlp_disaster_type)
        )

        # ── 6. Trend signal ───────────────────────────────────────────
        trend_event = self.trend_engine.add_signal(
            disaster_type=inp.nlp_disaster_type,
            location_key=location_key,
            location_name=inp.nlp_locations[0] if inp.nlp_locations else "unknown",
            confidence=inp.nlp_confidence,
            post_id=inp.post_id,
            raw_text=inp.raw_text,
        )

        trend_id = None
        insights: dict = {
            "cluster_stats":      cluster_stats,
            "cluster_multiplier": cluster_mult,
            "score_breakdown": {
                "nlp_confidence":    inp.nlp_confidence,
                "vision_confidence": inp.vision_confidence,
                "urgency_score":     inp.nlp_urgency_score,
                "final_score":       final_score,
            },
        }
        if is_duplicate:
            insights["deduplication"] = {
                "suppressed":       True,
                "cooldown_seconds": self._deduplicator.COOLDOWN_SECONDS,
                "location_key":     location_key,
            }
        if trend_event:
            trend_id = trend_event.trend_id
            insights["trend_event"] = {
                "trend_id":     trend_event.trend_id,
                "severity":     trend_event.severity,
                "signal_count": trend_event.signal_count,
                "is_new":       trend_event.is_new,
                "is_escalated": trend_event.is_escalated,
            }

        # ── 7. Build & store alert ────────────────────────────────────
        actions = RECOMMENDED_ACTIONS.get(inp.nlp_disaster_type, RECOMMENDED_ACTIONS["default"])
        alert = Alert(
            alert_id=alert_id,
            severity=severity,
            combined_confidence=final_score,
            disaster_type=inp.nlp_disaster_type,
            locations=inp.nlp_locations,
            organizations=inp.nlp_organizations,
            keywords=inp.nlp_keywords,
            recommended_actions=actions,
            insights=insights,
            source_platform=inp.source_platform,
            post_id=inp.post_id,
            author=inp.author,
            image_source=inp.image_source,
            nlp_confidence=inp.nlp_confidence,
            vision_confidence=inp.vision_confidence,
            created_at=created_at,
            raw_text=inp.raw_text,
            geocoded_locations=inp.nlp_geocoded_locations,
            is_duplicate=is_duplicate,
            trend_id=trend_id,
        )
        self._alert_store.append(alert)
        return alert

    # ------------------------------------------------------------------

    def clear_alerts(self) -> None:
        self._alert_store.clear()


# ─────────────────────────────────────────────────────────
# LEGACY STANDALONE FUNCTION  (kept for testing / __main__)
# ─────────────────────────────────────────────────────────

def process_tweet_full(
    tweet_text:    str,
    image_url:     str  = None,
    author_handle: str  = "unknown",
    is_verified:   bool = False,
    followers:     int  = 0,
) -> dict:
    """
    Full ensemble processing of one tweet.
    Returns final alert object ready for dashboard + notification.
    """
    # Deferred imports to avoid model loading at module import time
    from nlp.nlp_pipeline    import analyze_tweet
    from nlp.vision_analyzer import analyze_image

    # ── 1. NLP Analysis ──────────────────────────────────
    image_result = None
    if image_url:
        image_result = analyze_image(image_url)

    nlp_result = analyze_tweet(tweet_text, image_result)

    if not nlp_result["is_disaster"]:
        return {"alert_fired": False, "reason": "NLP: not disaster-related"}

    # ── 2. Source Credibility ─────────────────────────────
    credibility = score_source_credibility(author_handle, is_verified, followers)

    # ── 3. Temporal Clustering ────────────────────────────
    primary_location = (
        nlp_result["geocoded_locations"][0]["name"]
        if nlp_result["geocoded_locations"]
        else nlp_result["entities"]["locations"][0]["name"]
        if nlp_result["entities"]["locations"]
        else "unknown"
    )
    location_key = primary_location.lower().replace(" ", "_")

    cluster_engine.add_signal(
        location_key,
        nlp_result["category"],
        nlp_result["composite_score"]
    )
    cluster_stats = cluster_engine.get_cluster_stats(location_key, nlp_result["category"])

    # ── 4. Composite Final Score ──────────────────────────
    base_score   = nlp_result["composite_score"]
    cluster_mult = cluster_engine.get_cluster_severity_boost(cluster_stats["count"])
    cred_mult    = 0.8 + (credibility["credibility_score"] * 0.4)   # range: 0.8–1.2

    final_score = min(base_score * cluster_mult * cred_mult, 1.0)

    if final_score >= 0.65:   final_severity = "HIGH"
    elif final_score >= 0.40: final_severity = "MEDIUM"
    else:                     final_severity = "LOW"

    # ── 5. Deduplication ─────────────────────────────────
    alert_fired = deduplicator.should_alert(location_key, nlp_result["category"])

    # ── 6. Build Final Alert ──────────────────────────────
    alert = {
        "alert_fired":        alert_fired,
        "alert_id":           hashlib.md5(f"{tweet_text[:50]}{time.time()}".encode()).hexdigest()[:10],
        "timestamp":          datetime.utcnow().isoformat() + "Z",
        "is_disaster":        True,
        "category":           nlp_result["category"],
        "final_severity":     final_severity,
        "final_score":        round(final_score, 3),
        "nlp_confidence":     nlp_result["confidence"],
        "urgency":            nlp_result["urgency"],
        "entities":           nlp_result["entities"],
        "geocoded_locations": nlp_result["geocoded_locations"],
        "cluster_stats":      cluster_stats,
        "credibility":        credibility,
        "image_analysis":     image_result,
        "score_components": {
            "base_score":      round(base_score, 3),
            "cluster_multiplier": round(cluster_mult, 2),
            "credibility_multiplier": round(cred_mult, 2),
            "final_score":    round(final_score, 3),
        },
        "original_tweet":    tweet_text,
    }

    # ── 7. Generate Situation Brief ───────────────────────
    if alert_fired and final_severity in ["HIGH", "MEDIUM"]:
        alert["situation_brief"] = generate_situation_brief(alert)

    return alert


# ─────────────────────────────────────────────────────────
# TEST
# ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    tweets = [
        {
            "text":    "Flooding in Chennai Velachery area. Waist-deep water. People trapped. No rescue yet. SOS!!",
            "handle":  "@citizen_reporter",
            "verified": False,
            "followers": 340,
        },
        {
            "text":    "NDRF teams deployed in Velachery Chennai. Rescue ops underway. 23 people evacuated so far.",
            "handle":  "@ndmaindia",
            "verified": True,
            "followers": 1200000,
        },
        {
            "text":    "Had coffee this morning. Great start to the day!",
            "handle":  "@randomuser",
            "verified": False,
            "followers": 50,
        },
    ]

    print("=" * 65)
    print("ENSEMBLE ALERT ENGINE — LIVE PROCESSING")
    print("=" * 65)

    for t in tweets:
        print(f"\n📨 Tweet: {t['text'][:70]}...")
        result = process_tweet_full(
            tweet_text=t["text"],
            author_handle=t["handle"],
            is_verified=t["verified"],
            followers=t["followers"],
        )

        if result["alert_fired"]:
            print(f"  🚨 ALERT FIRED: {result['final_severity']}")
            print(f"  📊 Final score: {result['final_score']} (cluster x{result['score_components']['cluster_multiplier']})")
            print(f"  📍 Location:    {[g['name'] for g in result['geocoded_locations']]}")
            if "situation_brief" in result:
                print(f"  📋 Brief: {result['situation_brief']}")
        elif result.get("is_disaster"):
            print(f"  ⏳ Disaster detected but deduped (alert already fired for this area)")
        else:
            print(f"  ✅ Not a disaster tweet — skipped")