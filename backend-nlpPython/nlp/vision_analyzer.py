"""
COMPUTER VISION — Improved CLIP-based Disaster Image Analyzer  (v2)
---------------------------------------------------------------------
Upgrades over v1 (ResNet-50 / 18-prompt single-pass CLIP):
  • 2-stage CLIP classification
      Stage 1 — binary disaster / non-disaster (4 broad anchors)
      Stage 2 — per-category disaster type using cosine-similarity
                averaging (avoids softmax dilution from large prompt sets)
  • 60+ curated prompts across 9 disaster categories
  • Text-context cross-modal boosting
      When the originating tweet/post text is available, keyword hints
      are used to amplify matching category scores before the final
      decision — improving accuracy on ambiguous images.
  • Improved pixel-level heuristics
      Refined fire, smoke, flood, night/darkness, and structural
      damage detectors with better threshold calibration.
  • Per-category severity scoring
      Each disaster type has dedicated visual severity indicators.

Install: pip install torch torchvision transformers Pillow requests
"""

from __future__ import annotations

import io
import logging
import warnings
from typing import Optional

import numpy as np
import requests
import torch
from PIL import Image, ImageStat
from transformers import CLIPModel, CLIPProcessor

warnings.filterwarnings("ignore")
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# MODEL — lazy-loaded globals
# ─────────────────────────────────────────────────────────────────────────────

CLIP_MODEL_NAME = "openai/clip-vit-large-patch14"

_clip_model:     Optional[CLIPModel]    = None
_clip_processor: Optional[CLIPProcessor] = None
_device:         Optional[torch.device] = None
_vision_ready:   bool = False

# keep old names for backward compat
clip_model      = None
clip_processor  = None
device          = None


def _init_vision_models() -> None:
    global _clip_model, _clip_processor, _device, _vision_ready
    global clip_model, clip_processor, device   # backward compat aliases
    if _vision_ready:
        return
    print("Loading CLIP vision model (vit-large-patch14)…")
    _clip_model     = CLIPModel.from_pretrained(CLIP_MODEL_NAME)
    _clip_processor = CLIPProcessor.from_pretrained(CLIP_MODEL_NAME)
    _clip_model.eval()
    _device    = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    _clip_model = _clip_model.to(_device)
    # aliases
    clip_model     = _clip_model
    clip_processor = _clip_processor
    device         = _device
    _vision_ready  = True
    print("CLIP vision model loaded ✅\n")


# ─────────────────────────────────────────────────────────────────────────────
# PROMPT LIBRARY  (v2 — greatly expanded, category-organised)
# ─────────────────────────────────────────────────────────────────────────────

# Stage 1: binary disaster / normal anchors
STAGE1_DISASTER_ANCHORS = [
    "a photograph showing a natural disaster, emergency, or catastrophic event",
    "a photo showing extreme destruction, devastation, crisis, or mass casualties",
]
STAGE1_NORMAL_ANCHORS = [
    "a normal everyday photograph with no disaster or emergency",
    "a peaceful, ordinary photo of people engaged in daily activities",
]

# Stage 2: per-category prompts — used with cosine similarity (not softmax)
CATEGORY_PROMPTS: dict[str, list[str]] = {
    "flood": [
        "flooded streets with water completely submerging cars and roads",
        "rescue boats navigating through flooded residential neighbourhoods",
        "people wading through waist-deep muddy floodwater in urban streets",
        "submerged houses and buildings with only rooftops visible above water",
        "flood victims stranded on rooftops awaiting helicopter rescue",
        "brown muddy water rapidly flowing through a flooded city",
        "burst river banks with water spilling over levees into farmland",
    ],
    "fire": [
        "massive wildfire with towering orange flames and black smoke column",
        "forest fire burning hillside with orange glow visible at night",
        "building completely engulfed in flames with firefighters battling blaze",
        "charred, blackened remains of houses destroyed by wildfire",
        "aerial view of wildfire burning thousands of acres of forest",
        "industrial plant fire with thick toxic black smoke rising high",
        "ember shower from wildfire falling on residential neighbourhood",
    ],
    "earthquake": [
        "collapsed multi-storey building with concrete rubble and exposed rebar",
        "earthquake damage with severely cracked walls and fallen concrete slabs",
        "rescue workers searching through earthquake rubble for survivors",
        "tilted or leaning building about to collapse after seismic event",
        "cracked and buckled road surface with displaced asphalt",
        "destroyed residential neighbourhood with buildings reduced to rubble",
        "dust cloud rising from building collapse in urban area",
    ],
    "cyclone": [
        "cyclone or hurricane damage with large uprooted trees blocking roads",
        "storm surge flooding low-lying coastal area from tropical cyclone",
        "homes with roofs ripped off by hurricane-force winds",
        "aerial view of widespread cyclone destruction in coastal town",
        "corrugated metal sheets and debris scattered by powerful storm",
        "flooded streets combined with overturned vehicles from hurricane",
    ],
    "tsunami": [
        "massive ocean wave sweeping over coastal town infrastructure",
        "tsunami wave engulfing beachfront buildings and streets",
        "coastal area covered in large debris field after tsunami receded",
        "boats and vehicles swept far inland by tsunami wave",
        "destroyed harbour with vessels piled on shore after tsunami",
    ],
    "landslide": [
        "massive landslide of mud and rock covering an entire highway",
        "hillside collapse burying houses and structures in mud",
        "mudflow destroying village buildings after heavy rainfall",
        "rockslide completely blocking mountain road with boulders",
        "rain-saturated slope failure exposing bare earth and destroyed vegetation",
    ],
    "structural": [
        "bridge section collapsed into river below",
        "dam structure with water surging through catastrophic breach",
        "multi-storey building facade collapsed outward onto street",
        "industrial explosion with shattered glass and fire damage",
        "crane or scaffolding collapsed on city street",
    ],
    "evacuation": [
        "large crowds of displaced people at overcrowded emergency shelter",
        "long convoy of vehicles evacuating from approaching disaster zone",
        "displaced families carrying belongings and children fleeing disaster",
        "military helicopters conducting civilian rescue and evacuation operations",
        "emergency response teams in protective gear at disaster scene",
        "people being carried on stretchers by paramedics at disaster site",
    ],
}

# Flat disaster list and lookup used by legacy clip_classify_image()
DISASTER_PROMPTS: list[str] = [p for prompts in CATEGORY_PROMPTS.values() for p in prompts]

NON_DISASTER_PROMPTS: list[str] = [
    "normal sunny street scene with people casually walking",
    "beautiful landscape with clear blue sky and green hills",
    "food photography or clean restaurant interior",
    "smiling person taking a selfie outdoors",
    "sports event crowd cheering in stadium",
    "wedding ceremony or celebration gathering",
    "modern shopping mall or commercial interior",
    "normal dry highway with moving traffic",
    "tourist attraction with crowds enjoying sightseeing",
    "children playing happily in a park",
    "office meeting or corporate business event",
    "live music concert or entertainment performance",
]

ALL_PROMPTS: list[str]  = DISASTER_PROMPTS + NON_DISASTER_PROMPTS
NUM_DISASTER:       int = len(DISASTER_PROMPTS)

# Text-context keyword → category mapping for cross-modal boosting
CONTEXT_KEYWORDS: dict[str, str] = {
    "flood": "flood", "flooding": "flood", "submerged": "flood", "deluge": "flood",
    "wildfire": "fire", "fire": "fire", "blaze": "fire", "flames": "fire",
    "earthquake": "earthquake", "tremor": "earthquake", "quake": "earthquake",
    "cyclone": "cyclone", "hurricane": "cyclone", "typhoon": "cyclone", "storm": "cyclone",
    "tsunami": "tsunami", "tidal wave": "tsunami",
    "landslide": "landslide", "mudslide": "landslide", "avalanche": "landslide",
    "collapse": "structural", "explosion": "structural", "blast": "structural",
    "evacuate": "evacuation", "rescue": "evacuation", "stranded": "evacuation",
}


# ─────────────────────────────────────────────────────────────────────────────
# CLIP UTILITIES
# ─────────────────────────────────────────────────────────────────────────────

def _get_image_features(image: Image.Image) -> torch.Tensor:
    """Return L2-normalised image embedding (1 × D)."""
    _init_vision_models()
    inputs = _clip_processor(images=image, return_tensors="pt").to(_device)
    with torch.no_grad():
        feat = _clip_model.get_image_features(**inputs)
    return feat / feat.norm(dim=-1, keepdim=True)


def _get_text_features(prompts: list[str]) -> torch.Tensor:
    """Return L2-normalised text embeddings (N × D)."""
    _init_vision_models()
    inputs = _clip_processor(text=prompts, return_tensors="pt", padding=True, truncation=True).to(_device)
    with torch.no_grad():
        feat = _clip_model.get_text_features(**inputs)
    return feat / feat.norm(dim=-1, keepdim=True)


def _cosine_similarities(img_feat: torch.Tensor, txt_feat: torch.Tensor) -> np.ndarray:
    """Return cosine similarity scores as 1-D numpy array."""
    sims = (img_feat @ txt_feat.T).squeeze(0)
    return sims.cpu().numpy().astype(float)


# ─────────────────────────────────────────────────────────────────────────────
# STAGE 1: BINARY DISASTER / NORMAL CLASSIFICATION
# ─────────────────────────────────────────────────────────────────────────────

def _stage1_binary(img_feat: torch.Tensor) -> float:
    """
    Return P(disaster) in [0,1].
    Uses 4 broad anchors with softmax — clean binary decision.
    """
    all_anchors = STAGE1_DISASTER_ANCHORS + STAGE1_NORMAL_ANCHORS
    txt_feat = _get_text_features(all_anchors)
    sims     = _cosine_similarities(img_feat, txt_feat)
    logits   = torch.tensor(sims, dtype=torch.float32) * 100.0   # CLIP temp ≈ 100
    probs    = torch.softmax(logits, dim=0).numpy()
    return float(probs[:len(STAGE1_DISASTER_ANCHORS)].sum())


# ─────────────────────────────────────────────────────────────────────────────
# STAGE 2: PER-CATEGORY DISASTER TYPE SCORING
# ─────────────────────────────────────────────────────────────────────────────

def _stage2_category_scores(
    img_feat:     torch.Tensor,
    text_context: str = "",
) -> dict[str, float]:
    """
    For each disaster category compute the average cosine similarity between
    the image embedding and all category prompts.  Returns a dict of
    normalised scores in [0, 1].

    Text-context cross-modal boosting: if the associated post text contains
    keywords that imply a specific disaster type, that category's score
    receives a multiplicative boost (up to ×1.25).
    """
    raw: dict[str, float] = {}
    for category, prompts in CATEGORY_PROMPTS.items():
        txt_feat = _get_text_features(prompts)
        sims     = _cosine_similarities(img_feat, txt_feat)
        raw[category] = float(np.mean(sims))

    # Cross-modal text boost
    if text_context:
        text_lower = text_context.lower()
        for keyword, category in CONTEXT_KEYWORDS.items():
            if keyword in text_lower and category in raw:
                raw[category] = min(raw[category] * 1.25, 1.0)

    # Normalise so scores are comparable across categories
    min_s, max_s = min(raw.values()), max(raw.values())
    spread = max_s - min_s or 1e-9
    return {cat: round((s - min_s) / spread, 4) for cat, s in raw.items()}


# ─────────────────────────────────────────────────────────────────────────────
# LEGACY  clip_classify_image()  — kept for backward compat
# ─────────────────────────────────────────────────────────────────────────────

def clip_classify_image(image: Image.Image) -> dict:
    """
    Legacy single-pass CLIP classification (retained for backward compat).
    New code should call analyze_image() which uses the 2-stage approach.
    """
    _init_vision_models()
    inputs = _clip_processor(
        text=ALL_PROMPTS, images=image, return_tensors="pt", padding=True, truncation=True,
    ).to(_device)
    with torch.no_grad():
        logits_per_image = _clip_model(**inputs).logits_per_image
        probs = logits_per_image.softmax(dim=1).squeeze().cpu().numpy()

    disaster_mass     = float(probs[:NUM_DISASTER].sum())
    non_disaster_mass = float(probs[NUM_DISASTER:].sum())
    top_idx   = int(np.argmax(probs))
    return {
        "disaster_probability":     round(disaster_mass * 100, 1),
        "non_disaster_probability": round(non_disaster_mass * 100, 1),
        "best_match_description":   ALL_PROMPTS[top_idx],
        "best_match_confidence":    round(float(probs[top_idx]) * 100, 1),
        "all_prompt_scores": {p: round(float(v) * 100, 2) for p, v in zip(ALL_PROMPTS, probs)},
    }


# ─────────────────────────────────────────────────────────────────────────────
# IMPROVED PIXEL-LEVEL FEATURE EXTRACTION
# ─────────────────────────────────────────────────────────────────────────────

def extract_visual_features(image: Image.Image) -> dict:
    """
    Low-level heuristic signals extracted via pixel analysis.
    Improved v2: better fire/smoke/water detection, darkness & crowd proxies.
    """
    rgb = image.convert("RGB")
    arr = np.array(rgb, dtype=np.float32)
    r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]

    # ── Fire: bright orange/yellow, intense heat glow ──────────────────────
    # Classic flame: high R, moderate G, low B — and brighter than surroundings
    fire_mask = (r > 160) & (g < 140) & (b < 80) & (r > g * 1.3)
    fire_ratio = float(fire_mask.mean())

    # ── Ember/glow: dark-scene with localised red/orange ───────────────────
    brightness = (r + g + b) / 3.0
    glow_mask  = (r > 150) & (g < 100) & (b < 60) & (brightness < 120)
    glow_ratio = float(glow_mask.mean())

    # ── Smoke: grey-brown, low saturation, mid-dark brightness ─────────────
    diff_rg = np.abs(r - g)
    diff_gb = np.abs(g - b)
    smoke_mask = (diff_rg < 30) & (diff_gb < 30) & (r > 60) & (r < 180)
    smoke_ratio = float(smoke_mask.mean())

    # ── Flood water: murky brown-grey or blue-grey, wide spread ────────────
    brown_water = (r > g) & (g > b) & (np.abs(r.astype(int) - g.astype(int)) < 40) & (r < 170)
    blue_water  = (b > r) & (b > g) & (b > 70) & (b < 200)
    water_ratio = float((brown_water | blue_water).mean())

    # ── Sky / ambient darkness (top quarter of image) ──────────────────────
    top = arr[: arr.shape[0] // 4, :, :]
    sky_brightness = float(top.mean())

    # ── Overall scene darkness ─────────────────────────────────────────────
    overall_brightness = float(brightness.mean())

    # ── Structural chaos: high edge variance ──────────────────────────────
    stat = ImageStat.Stat(rgb)
    complexity = float(np.mean(stat.stddev))

    # ── Colour desaturation (disaster scenes often grey/dusty) ─────────────
    saturation = float(np.mean(np.max(arr, axis=2) - np.min(arr, axis=2)))

    return {
        "fire_pixel_ratio":    round(fire_ratio, 4),
        "glow_pixel_ratio":    round(glow_ratio, 4),
        "smoke_pixel_ratio":   round(smoke_ratio, 4),
        "water_pixel_ratio":   round(water_ratio, 4),
        "sky_brightness":      round(sky_brightness, 1),
        "overall_brightness":  round(overall_brightness, 1),
        "image_complexity":    round(complexity, 1),
        "colour_saturation":   round(saturation, 1),
    }


# ─────────────────────────────────────────────────────────────────────────────
# SEVERITY SCORING  (v2 — per-category visual cues)
# ─────────────────────────────────────────────────────────────────────────────

def score_visual_severity(
    disaster_prob:    float,
    category:         str,
    visual_features:  dict,
) -> str:
    """
    Determine visual severity (LOW / MEDIUM / HIGH) using CLIP confidence
    combined with category-specific pixel cues.
    """
    score = 0

    # CLIP confidence contribution
    if disaster_prob >= 80:   score += 4
    elif disaster_prob >= 60: score += 3
    elif disaster_prob >= 45: score += 2
    elif disaster_prob >= 30: score += 1

    vf = visual_features
    cat = category.lower()

    # Category-specific heuristic boosts
    if cat == "fire":
        if vf["fire_pixel_ratio"]  > 0.08:  score += 3
        elif vf["fire_pixel_ratio"] > 0.03: score += 2
        if vf["glow_pixel_ratio"]  > 0.05:  score += 1
        if vf["smoke_pixel_ratio"] > 0.15:  score += 2
        elif vf["smoke_pixel_ratio"] > 0.08: score += 1

    elif cat == "flood":
        if vf["water_pixel_ratio"] > 0.30:  score += 3
        elif vf["water_pixel_ratio"] > 0.15: score += 2
        elif vf["water_pixel_ratio"] > 0.07: score += 1

    elif cat in ("earthquake", "structural"):
        if vf["smoke_pixel_ratio"] > 0.12:  score += 1
        if vf["image_complexity"]  > 70:    score += 2   # rubble = high chaos

    elif cat == "cyclone":
        if vf["overall_brightness"] < 70:   score += 1   # dark storm sky
        if vf["image_complexity"]   > 65:   score += 2

    # Generic cross-category signals
    if vf["image_complexity"]    > 75:   score += 1   # chaotic scene
    if vf["sky_brightness"]      < 70:   score += 1   # dark/overcast sky
    if vf["colour_saturation"]   < 40:   score += 1   # grey/dusty scene (dust clouds)

    if score >= 8:  return "HIGH"
    if score >= 5:  return "MEDIUM"
    return "LOW"


# ─────────────────────────────────────────────────────────────────────────────
# MASTER FUNCTION
# ─────────────────────────────────────────────────────────────────────────────

def analyze_image(image_input, text_context: str = "") -> dict:
    """
    Accepts: URL string or PIL Image object.
    Optional text_context: the originating tweet/post text for cross-modal boosting.
    Returns: full visual analysis result dict.

    Two-stage CLIP approach:
      1. Binary disaster probability from 4 broad anchors
      2. Per-category cosine similarity to 60+ targeted prompts
    """
    try:
        if isinstance(image_input, str):
            resp  = requests.get(image_input, timeout=10)
            image = Image.open(io.BytesIO(resp.content)).convert("RGB")
        elif isinstance(image_input, Image.Image):
            image = image_input.convert("RGB")
        else:
            return {"error": "Invalid input — provide URL string or PIL Image"}

        # Resize: CLIP works best at 336 × 336 for vit-large-patch14
        image = image.resize((336, 336), Image.LANCZOS)

        _init_vision_models()

        # ── Stage 1: binary disaster probability ──────────────────────────
        img_feat      = _get_image_features(image)
        disaster_prob = _stage1_binary(img_feat)   # 0.0 – 1.0

        # ── Stage 2: category classification ──────────────────────────────
        cat_scores  = _stage2_category_scores(img_feat, text_context)
        top_category = max(cat_scores, key=cat_scores.get)
        top_score    = cat_scores[top_category]

        # ── Pixel heuristics ───────────────────────────────────────────────
        visual_features = extract_visual_features(image)

        # ── Severity ───────────────────────────────────────────────────────
        disaster_pct = disaster_prob * 100.0
        severity     = score_visual_severity(disaster_pct, top_category, visual_features)

        is_disaster = disaster_prob >= 0.45

        return {
            "has_image":               True,
            "is_disaster_image":       is_disaster,
            "disaster_probability":    f"{round(disaster_pct, 1)}%",
            "visual_severity":         severity,
            "disaster_category":       top_category if is_disaster else "none",
            "category_confidence":     round(top_score, 3),
            "all_category_scores":     cat_scores,
            "visual_signals": {
                "fire_detected":   visual_features["fire_pixel_ratio"] > 0.05,
                "glow_detected":   visual_features["glow_pixel_ratio"] > 0.04,
                "smoke_detected":  visual_features["smoke_pixel_ratio"] > 0.12,
                "water_detected":  visual_features["water_pixel_ratio"] > 0.15,
                "dark_conditions": visual_features["sky_brightness"]    < 80,
                "chaotic_scene":   visual_features["image_complexity"]  > 65,
                "dusty_grey":      visual_features["colour_saturation"] < 40,
            },
            "raw_features": visual_features,
            # Legacy field — keep for downstream compat
            "best_match": f"{top_category} disaster scene" if is_disaster else "non-disaster scene",
        }

    except Exception as e:
        logger.exception("analyze_image failed: %s", e)
        return {"has_image": False, "error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# CLASS-BASED WRAPPER  (used by api.py)
# ─────────────────────────────────────────────────────────────────────────────

_CATEGORY_TO_TYPE: dict[str, str] = {
    "flood":       "flood",
    "fire":        "fire",
    "earthquake":  "earthquake",
    "cyclone":     "cyclone",
    "tsunami":     "tsunami",
    "landslide":   "landslide",
    "structural":  "infrastructure",
    "evacuation":  "unknown",   # evacuation doesn't map to a single disaster type
}


class VisionAnalyzer:
    """
    Stateless wrapper around analyze_image().
    analyze_url() is called in a thread executor — safe for concurrent use.

    New in v2: accepts optional text_context for cross-modal boosting.
    """

    def analyze_url(self, url: str, text_context: str = ""):
        """Download and analyze an image from URL.  Returns VisionResult."""
        from nlp.models import VisionResult

        result = analyze_image(url, text_context=text_context)

        if result.get("error") or not result.get("has_image"):
            return VisionResult(is_disaster_related=False, disaster_type="none", confidence=0.0)

        prob_str = result.get("disaster_probability", "0%")
        prob     = float(str(prob_str).replace("%", "")) / 100.0

        category      = result.get("disaster_category", "unknown")
        disaster_type = _CATEGORY_TO_TYPE.get(category, "unknown") if result.get("is_disaster_image") else "none"

        return VisionResult(
            is_disaster_related=result.get("is_disaster_image", False),
            disaster_type=disaster_type,
            confidence=prob,
        )


# ─────────────────────────────────────────────────────────────────────────────
# QUICK TEST
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    test_cases = [
        (
            "https://upload.wikimedia.org/wikipedia/commons/thumb/f/f6/2004_Indonesian_Tsunami.jpg/800px-2004_Indonesian_Tsunami.jpg",
            "tsunami wave hits coastal town",
        ),
        (
            "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a7/Camponotus_flavomarginatus_ant.jpg/640px-Camponotus_flavomarginatus_ant.jpg",
            "",
        ),
    ]
    for url, ctx in test_cases:
        print(f"\nAnalyzing: …{url[-55:]}")
        res = analyze_image(url, text_context=ctx)
        print(f"  Disaster image : {res.get('is_disaster_image')}")
        print(f"  Probability    : {res.get('disaster_probability')}")
        print(f"  Category       : {res.get('disaster_category')}")
        print(f"  Severity       : {res.get('visual_severity')}")
        print(f"  Visual signals : {res.get('visual_signals')}")
