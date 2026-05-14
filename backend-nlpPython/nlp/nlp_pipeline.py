"""
ADVANCED NLP PIPELINE
- Fine-tuned BERTweet for multi-class disaster classification
- HuggingFace NER for location + person entity extraction
- Custom urgency lexicon with intensity scoring
- Place-name geocoding via Nominatim (no API key needed)
- Falls back to zero-shot BART if fine-tuned model not trained yet
"""

import os
import re
import time
import warnings
from functools import lru_cache
from typing import Optional

import numpy as np
import spacy
import torch
from geopy.exc import GeocoderTimedOut
from geopy.geocoders import Nominatim
from transformers import (
    AutoModelForSequenceClassification,
    AutoTokenizer,
    pipeline,
)

warnings.filterwarnings("ignore")


# ─────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────

FINE_TUNED_PATH = "./bertweet_disaster_model"
NER_MODEL       = "dslim/bert-base-NER"
FALLBACK_MODEL  = "facebook/bart-large-mnli"

LABEL2ID = {
    "not_disaster": 0, "flood": 1, "earthquake": 2,
    "fire": 3, "cyclone": 4, "infrastructure": 5, "medical": 6
}
ID2LABEL = {v: k for k, v in LABEL2ID.items()}


# ─────────────────────────────────────────────────────────
# LAZY-LOADED GLOBALS  (None until _init_nlp_models() runs)
# ─────────────────────────────────────────────────────────

device:         object = None
USE_FINE_TUNED: bool   = False
tokenizer               = None
clf_model               = None
zero_shot               = None
ner_pipeline            = None
nlp_spacy               = None
geolocator              = None
_nlp_ready:     bool   = False


# ─────────────────────────────────────────────────────────
# MODEL INITIALISER  (called once by DisasterNLPPipeline)
# ─────────────────────────────────────────────────────────

def _init_nlp_models() -> None:
    global device, USE_FINE_TUNED, tokenizer, clf_model
    global zero_shot, ner_pipeline, nlp_spacy, geolocator, _nlp_ready

    if _nlp_ready:
        return

    print("Loading NLP models...")
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    USE_FINE_TUNED = os.path.isdir(FINE_TUNED_PATH)
    if USE_FINE_TUNED:
        print(f"  Loading fine-tuned BERTweet from {FINE_TUNED_PATH}")
        tokenizer = AutoTokenizer.from_pretrained(FINE_TUNED_PATH, use_fast=False)
        clf_model = AutoModelForSequenceClassification.from_pretrained(FINE_TUNED_PATH).to(device)
        clf_model.eval()
    else:
        print("  Fine-tuned model not found. Using zero-shot BART fallback.")
        zero_shot = pipeline("zero-shot-classification", model=FALLBACK_MODEL)

    print("  Loading NER model (BERT-NER)...")
    ner_pipeline = pipeline(
        "ner",
        model=NER_MODEL,
        aggregation_strategy="simple",
    )

    print("  Loading spaCy...")
    nlp_spacy = spacy.load("en_core_web_sm")

    geolocator = Nominatim(user_agent="disaster_alert_system_v2", timeout=5)

    _nlp_ready = True
    print("All NLP models loaded\n")


# ─────────────────────────────────────────────────────────
# STEP 1 — TWEET CLEANING
# ─────────────────────────────────────────────────────────

def clean_tweet(text: str) -> str:
    text = re.sub(r"http\S+|www\S+", " ", text)
    text = re.sub(r"@\w+", " ", text)
    text = re.sub(r"#(\w+)", r"\1", text)
    text = re.sub(r"[^\w\s\.,!?'-]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ─────────────────────────────────────────────────────────
# STEP 2 — DISASTER CLASSIFICATION
# ─────────────────────────────────────────────────────────

DISASTER_LABELS = ["flood", "earthquake", "fire", "cyclone", "infrastructure", "medical"]

def classify_disaster(text: str) -> dict:
    cleaned = clean_tweet(text)

    if USE_FINE_TUNED:
        inputs = tokenizer(
            cleaned,
            return_tensors="pt",
            max_length=128,
            truncation=True,
            padding="max_length"
        ).to(device)

        with torch.no_grad():
            logits = clf_model(**inputs).logits
            probs  = torch.softmax(logits, dim=-1)[0]

        probs_np  = probs.cpu().numpy()
        top_idx   = int(np.argmax(probs_np))
        top_label = ID2LABEL[top_idx]
        top_prob  = float(probs_np[top_idx])
        is_disaster = top_label != "not_disaster"
        all_probs = {ID2LABEL[i]: round(float(p) * 100, 2) for i, p in enumerate(probs_np)}

    else:
        result      = zero_shot(cleaned, candidate_labels=["disaster", "not disaster"])
        top_label   = result["labels"][0]
        top_prob    = result["scores"][0]
        is_disaster = top_label == "disaster"
        all_probs   = {"disaster": round(top_prob * 100, 2), "not_disaster": round((1 - top_prob) * 100, 2)}

    return {
        "is_disaster":    is_disaster,
        "category":       top_label if is_disaster else "not_disaster",
        "confidence":     round(top_prob * 100, 1),
        "all_categories": all_probs,
        "cleaned_text":   cleaned,
    }


# ─────────────────────────────────────────────────────────
# STEP 3 — ENTITY EXTRACTION
# ─────────────────────────────────────────────────────────

def extract_entities(text: str) -> dict:
    hf_entities = ner_pipeline(text)

    locations, persons, orgs = [], [], []
    for ent in hf_entities:
        val = ent["word"].strip()
        if not val or len(val) < 2:
            continue
        if ent["entity_group"] in ["LOC", "GPE"]:
            locations.append({"name": val, "score": round(ent["score"], 3)})
        elif ent["entity_group"] == "PER":
            persons.append(val)
        elif ent["entity_group"] == "ORG":
            orgs.append(val)

    doc = nlp_spacy(text)
    for ent in doc.ents:
        if ent.label_ in ["GPE", "LOC", "FAC"]:
            existing = [l["name"].lower() for l in locations]
            if ent.text.lower() not in existing:
                locations.append({"name": ent.text, "score": 0.7})

    seen = set()
    unique_locations = []
    for loc in locations:
        key = loc["name"].lower()
        if key not in seen:
            seen.add(key)
            unique_locations.append(loc)

    unique_locations.sort(key=lambda x: x["score"], reverse=True)

    return {
        "locations":     unique_locations,
        "persons":       list(set(persons)),
        "organizations": list(set(orgs)),
    }


# ─────────────────────────────────────────────────────────
# STEP 4 — GEOCODING
# ─────────────────────────────────────────────────────────

@lru_cache(maxsize=512)
def geocode_location(place_name: str) -> Optional[dict]:
    try:
        time.sleep(0.5)   # Nominatim rate limit: 1 req/sec
        location = geolocator.geocode(place_name, exactly_one=True, timeout=5)
        if location:
            return {
                "name":    place_name,
                "lat":     round(location.latitude, 5),
                "lng":     round(location.longitude, 5),
                "address": location.address,
            }
    except GeocoderTimedOut:
        pass
    return None


def geocode_all_locations(entities: dict) -> list:
    results = []
    for loc in entities["locations"][:3]:
        coords = geocode_location(loc["name"])
        if coords:
            coords["ner_confidence"] = loc["score"]
            results.append(coords)
    return results


# ─────────────────────────────────────────────────────────
# STEP 5 — URGENCY SCORING
# ─────────────────────────────────────────────────────────

URGENCY_LEXICON = {
    "sos": 3, "mayday": 3, "dying": 3, "dead bodies": 3,
    "multiple deaths": 3, "casualties": 3, "critical": 3,
    "trapped": 2, "stranded": 2, "rescue": 2, "emergency": 2,
    "urgent": 2, "immediately": 2, "help": 2, "need rescue": 2,
    "no food": 2, "no water": 2, "no power": 2, "collapsed": 2,
    "evacuate": 1, "warning": 1, "alert": 1, "danger": 1,
    "damage": 1, "destroyed": 1, "blocked": 1, "closed": 1,
}

def score_urgency(text: str) -> dict:
    text_lower = text.lower()
    score = 0
    matched_terms = []

    for term, weight in URGENCY_LEXICON.items():
        if term in text_lower:
            score += weight
            matched_terms.append(term)

    normalized = min(score, 10) / 10.0

    return {
        "urgency_score":         round(normalized, 2),
        "raw_score":             score,
        "matched_urgency_terms": matched_terms,
    }


# ─────────────────────────────────────────────────────────
# STEP 6 — FINAL SEVERITY SCORING
# ─────────────────────────────────────────────────────────

def compute_severity(
    classification: dict,
    urgency: dict,
    geocoded: list,
    visual_result: dict = None
) -> dict:
    score = 0.0

    confidence = classification["confidence"] / 100
    score += confidence * 0.40
    score += urgency["urgency_score"] * 0.35

    if geocoded:
        score += 0.15

    if visual_result and visual_result.get("is_disaster_image"):
        visual_map   = {"HIGH": 1.0, "MEDIUM": 0.6, "LOW": 0.3}
        visual_score = visual_map.get(visual_result.get("visual_severity", "LOW"), 0)
        score += visual_score * 0.10

    if score >= 0.65:   severity = "HIGH"
    elif score >= 0.40: severity = "MEDIUM"
    else:               severity = "LOW"

    visual_weight = 0.0
    if visual_result:
        try:
            visual_weight = round(float(str(visual_result.get("disaster_probability", "0%")).replace("%", "")) / 100 * 0.10, 3)
        except (ValueError, TypeError):
            visual_weight = 0.0

    return {
        "severity":        severity,
        "composite_score": round(score, 3),
        "breakdown": {
            "model_weight":    round(confidence * 0.40, 3),
            "urgency_weight":  round(urgency["urgency_score"] * 0.35, 3),
            "location_weight": 0.15 if geocoded else 0.0,
            "visual_weight":   visual_weight,
        }
    }


# ─────────────────────────────────────────────────────────
# MASTER FUNCTION
# ─────────────────────────────────────────────────────────

def analyze_tweet(tweet: str, image_result: dict = None) -> dict:
    classification = classify_disaster(tweet)

    if not classification["is_disaster"]:
        return {
            "is_disaster": False,
            "category":    "not_disaster",
            "confidence":  f"{classification['confidence']}%",
            "message":     "Not a disaster-related post. No alert generated."
        }

    entities        = extract_entities(tweet)
    geocoded        = geocode_all_locations(entities)
    urgency         = score_urgency(tweet)
    severity_result = compute_severity(classification, urgency, geocoded, image_result)

    return {
        "is_disaster":        True,
        "category":           classification["category"],
        "confidence":         f"{classification['confidence']}%",
        "all_categories":     classification["all_categories"],
        "severity":           severity_result["severity"],
        "composite_score":    severity_result["composite_score"],
        "score_breakdown":    severity_result["breakdown"],
        "urgency": {
            "score":        urgency["urgency_score"],
            "matched_terms": urgency["matched_urgency_terms"],
        },
        "entities": {
            "locations":     entities["locations"],
            "persons":       entities["persons"],
            "organizations": entities["organizations"],
        },
        "geocoded_locations":  geocoded,
        "has_image_analysis":  image_result is not None,
        "image_analysis":      image_result,
        "original_tweet":      tweet,
        "cleaned_text":        classification["cleaned_text"],
    }


# ─────────────────────────────────────────────────────────
# CLASS-BASED WRAPPER  (used by api.py)
# ─────────────────────────────────────────────────────────

class DisasterNLPPipeline:
    def __init__(self) -> None:
        _init_nlp_models()

    def analyze(self, text: str):
        from nlp.models import NLPResult

        result = analyze_tweet(text)

        if not result["is_disaster"]:
            raw_conf = result.get("confidence", "0%")
            conf = float(str(raw_conf).replace("%", "")) / 100.0
            return NLPResult(
                is_disaster_related=False,
                disaster_type="none",
                classification_confidence=conf,
                urgency_score=0.0,
                locations=[],
                organizations=[],
                keywords_found=[],
                geocoded_locations=[],
            )

        raw_conf = result.get("confidence", "0%")
        conf = float(str(raw_conf).replace("%", "")) / 100.0

        return NLPResult(
            is_disaster_related=True,
            disaster_type=result["category"],
            classification_confidence=conf,
            urgency_score=result["urgency"]["score"],
            locations=[loc["name"] for loc in result["entities"]["locations"]],
            organizations=result["entities"]["organizations"],
            keywords_found=result["urgency"]["matched_terms"],
            geocoded_locations=result.get("geocoded_locations", []),
        )


def reload_model() -> bool:
    global USE_FINE_TUNED, tokenizer, clf_model
    import logging as _log
    _logger = _log.getLogger(__name__)

    if not os.path.isdir(FINE_TUNED_PATH):
        _logger.warning("reload_model(): %s not found — skipping.", FINE_TUNED_PATH)
        return False
    try:
        _logger.info("Reloading fine-tuned BERTweet from %s", FINE_TUNED_PATH)
        tokenizer = AutoTokenizer.from_pretrained(FINE_TUNED_PATH, use_fast=False)
        clf_model = AutoModelForSequenceClassification.from_pretrained(FINE_TUNED_PATH).to(device)
        clf_model.eval()
        USE_FINE_TUNED = True
        _logger.info("Fine-tuned model reloaded")
        return True
    except Exception as exc:
        _logger.error("Model reload failed: %s", exc)
        return False


if __name__ == "__main__":
    tests = [
        "Massive flooding in Chennai near Velachery! People trapped on rooftops, no food no water, SOS rescue needed urgently #ChennaiFloods",
        "Just had amazing biryani at a restaurant in Hyderabad, highly recommend to everyone!",
        "Building collapsed in Dharavi Mumbai. At least 8 people trapped under debris. NDRF team please respond. Urgent.",
    ]

    for tweet in tests:
        print(f"\nTWEET: {tweet[:80]}...")
        result = analyze_tweet(tweet)
        if result["is_disaster"]:
            print(f"  Disaster: {result['category']} | Confidence: {result['confidence']}")
            print(f"  Severity: {result['severity']} (score: {result['composite_score']})")
            print(f"  Locations: {[l['name'] for l in result['entities']['locations']]}")
            print(f"  Geocoded: {[(g['name'], g['lat'], g['lng']) for g in result['geocoded_locations']]}")
            print(f"  Urgency terms: {result['urgency']['matched_terms']}")
        else:
            print("  Not a disaster post")
