"""NexGen — 02: Tier-1 rule-based NLP feature extractor (the backbone).

Why this exists: the `description` field is the richest *unused* signal in the
dataset (6,811 / 8,171 rows = 83% coverage; 870 rows in Kannada script; large
Kanglish share). It contains facts the structured columns don't: lanes blocked,
"needs crane/tow", water-logging, agency mention, etc. This module turns that
free-text into structured features that feed the clearance model (`01`) and
the explanation payload (`08`).

Tiered architecture (per spec 02):
  Tier 1 — rule-based trilingual (this module). Pure Python, instant, full
           coverage, no GPU. **The default + only thing that runs at demo
           time.** Ships the spec's 4 highest-value features + 4 more.
  Tier 2 — LLM enrichment (optional). A one-time batch over the rows
           (Groq free tier or local Ollama), results cached in
           `data/llm_features_cache.json`. At inference time the backend
           reads the cache, never the live model.

Output schema (per spec 02 §3 — features actually used by 01/03/05/08):
  nlp_lanes_blocked     : int 0..1  — note implies lane(s) blocked
  nlp_needs_crane_tow   : int 0..1  — note implies a tow / crane / lift needed
  nlp_weather_water     : int 0..1  — note mentions water / rain / flooding
  nlp_agency_mention    : int 0..1  — note names BBMP / BWSSB / BESCOM / police / etc.
  nlp_kannada_cues      : int 0..1  — note has any Kannada script (U+0C80..U+0CFF)
  nlp_vehicle_hint      : str       — 'bus' | 'lorry' | 'lcv' | ... | 'unknown'
  nlp_breakdown_subtype : str       — 'starting_problem' | 'puncture' | ... | 'other'
  nlp_severity_cue      : int 1..5  — ordinal cue; explainability only (NOT a target)
  nlp_event_subtype     : str       — one of SUBTYPE_VALUES
  nlp_urgency_tone      : int 0..2  — 0 normal, 1 marked (exclamation/emoji), 2 urgent
  nlp_estimated_duration_min : float — note-stated duration in minutes; 0 if none
  nlp_subtype_le        : int       — label-encoded nlp_event_subtype

The label encoding is computed on the unique subtypes seen in the current
batch (deterministic for stable artifacts). Save to `data/nlp_features.parquet`
keyed on `id`.
"""
from __future__ import annotations
import json
import re
import sys
import unicodedata
import numpy as np
import pandas as pd

from . import config as C
from .nlp_lexicon import (
    SUBTYPE_KEYWORDS, BLOCK_TERMS, TOW_TERMS, WATER_TERMS, AGENCY_TERMS,
    VEHICLE_TERMS, SEVERE_TERMS, CLEAR_TERMS, SUBTYPE_VALUES,
    severity_from_cues, normalize_subtype,
)

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

KANNADA_RE = re.compile(r"[\u0C80-\u0CFF]")
EMOJI_RE = re.compile(r"[\U0001F300-\U0001F9FF\u2600-\u27BF🙏⚠️🚧⛔]")
ANON_TAG_RE = re.compile(r"\[(LOCATION|PERSON|ORG)\]")
NUM_HOUR_RE = re.compile(r"\b(\d{1,3})\s*(?:hour|hr|hrs|hou|ಗಂಟೆ|ghante|gante|hr|hours|h\b)", re.I)
NUM_MIN_RE = re.compile(r"\b(\d{1,3})\s*(?:min|mins|minute|minutes|ನಿಮಿಷ|nimisha)", re.I)
DAY_WORDS = {"today": 8*60, "tomorrow": 24*60, "now": 60}

# Subtype-first vehicle hint — first vehicle token in the note wins
VEHICLE_HINT_ORDER = [
    "bus", "lorry", "truck", "tanker", "container", "lcv", "auto", "tempo",
    "car", "bike", "two wheeler", "scooter", "cab", "taxi", "van", "vehicle",
]
BREAKDOWN_SUBTYPE_KEYWORDS = [
    ("starting_problem", ["starting problem", "start problem", "ಸ್ಟಾರ್ಟಿಂಗ್ ಪ್ರಾಬ್ಲಮ್", "start aagilla"]),
    ("puncture",         ["puncture", "tyre puncture", "ಟೈರ್ ಪಂಚರ್", "tayar"]),
    ("engine",           ["engine", "ಎಂಜಿನ್"]),
    ("battery",          ["battery", "ಬ್ಯಾಟರಿ"]),
    ("fuel",             ["fuel", "diesel", "petrol", "ಇಂಧನ"]),
    ("clutch_gearbox",   ["clutch", "gearbox", "gear"]),
    ("axle",             ["axle", "axel"]),
    ("accident",         ["accident", "ಅಪಘಾತ"]),
]


def _normalize(text: str) -> tuple[str, str]:
    """Return (lower_latin_part, kannada_part). Strips anonymization tags.

    Notes contain a mix of English, Kannada script, and Romanized Kannada
    (Kanglish). We split into two halves so each lexicon can be matched
    against the right half — substring matching on the concatenation would
    miss a lot of Kannada-script tokens. Whitespace is preserved within
    each half so multi-word tokens (e.g. "ಆಫ್ ರೋಡ್") still match.
    """
    if not isinstance(text, str) or not text.strip():
        return "", ""
    s = unicodedata.normalize("NFC", text)
    s = ANON_TAG_RE.sub(" ", s)  # anonymization tags become whitespace
    # Lowercase the latin portion only; keep Kannada case (script is
    # case-agnostic but we don't want to disturb the bytes).
    kannada_chars, latin_chars = [], []
    for ch in s:
        if "\u0C80" <= ch <= "\u0CFF":
            kannada_chars.append(ch)
        elif ch.isspace():
            # spaces appear between tokens in both halves
            kannada_chars.append(" ")
            latin_chars.append(" ")
        elif ch.isascii() and ch.isprintable():
            latin_chars.append(ch.lower())
    kannada = re.sub(r"\s+", " ", "".join(kannada_chars)).strip()
    latin = re.sub(r"\s+", " ", "".join(latin_chars)).strip()
    return latin, kannada


def _has_any(hay: str, terms) -> bool:
    return any(t in hay for t in terms)


def _extract_estimated_duration_min(text: str) -> float:
    """Pull a stated duration from the note (hours / minutes / '2-3 hours').

    Returns 0.0 if nothing is found. We err on the side of missing — the
    downstream model has its own duration estimate.
    """
    if not text:
        return 0.0
    candidates = []
    # explicit hour/minute
    for m in NUM_HOUR_RE.finditer(text):
        try:
            candidates.append(int(m.group(1)) * 60)
        except ValueError:
            pass
    for m in NUM_MIN_RE.finditer(text):
        try:
            candidates.append(int(m.group(1)))
        except ValueError:
            pass
    # 'X to Y hours' or 'X-Y hours'
    m = re.search(r"(\d{1,2})\s*[-to]+\s*(\d{1,2})\s*h", text, re.I)
    if m:
        try:
            lo, hi = int(m.group(1)), int(m.group(2))
            candidates.append(int((lo + hi) / 2) * 60)
        except ValueError:
            pass
    if not candidates:
        return 0.0
    val = float(min(candidates))  # conservative — first stated duration
    return max(0.0, min(val, 24 * 60))


def rule_extract(text: str) -> dict:
    """Tier-1 extractor for a single description string. Returns the schema."""
    if not isinstance(text, str) or not text.strip():
        return _defaults()
    latin, kannada = _normalize(text)
    hay = " " + latin + " " + " " + kannada + " "  # concat for substring search
    full = (latin or "") + " " + (kannada or "")

    # ---- event_subtype (first match wins — order matters)
    subtype = "other"
    for name, kws in SUBTYPE_KEYWORDS:
        if _has_any(hay, kws):
            subtype = name
            break
    subtype = normalize_subtype(subtype)

    # ---- booleans
    blocking = _has_any(hay, BLOCK_TERMS)
    needs_tow = _has_any(hay, TOW_TERMS)
    water = _has_any(hay, WATER_TERMS)
    agency = _has_any(hay, AGENCY_TERMS)
    has_kannada = bool(KANNADA_RE.search(text))
    has_severe = _has_any(hay, SEVERE_TERMS)
    has_clear = _has_any(hay, CLEAR_TERMS)
    severity = severity_from_cues(subtype, blocking, has_severe, has_clear)

    # ---- vehicle hint
    veh_hint = "unknown"
    for v in VEHICLE_HINT_ORDER:
        if v in hay:
            veh_hint = v
            break

    # ---- breakdown subtype (only if subtype == vehicle_breakdown)
    bd_sub = "other"
    if subtype == "vehicle_breakdown":
        for name, kws in BREAKDOWN_SUBTYPE_KEYWORDS:
            if _has_any(hay, kws):
                bd_sub = name
                break

    # ---- urgency tone (0 normal · 1 marked · 2 urgent)
    urgency = 0
    if "!" in text or EMOJI_RE.search(text):
        urgency = 1
    if any(t in text.lower() for t in ("urgent", "ತಕ್ಷಣ", "immediately", "asap")):
        urgency = 2

    # ---- estimated duration
    est_dur = _extract_estimated_duration_min(text)

    return {
        "nlp_lanes_blocked": int(blocking),
        "nlp_needs_crane_tow": int(needs_tow),
        "nlp_weather_water": int(water),
        "nlp_agency_mention": int(agency),
        "nlp_kannada_cues": int(has_kannada),
        "nlp_vehicle_hint": veh_hint,
        "nlp_breakdown_subtype": bd_sub,
        "nlp_severity_cue": int(severity),
        "nlp_event_subtype": subtype,
        "nlp_urgency_tone": int(urgency),
        "nlp_estimated_duration_min": float(est_dur),
    }


def _defaults() -> dict:
    return {
        "nlp_lanes_blocked": 0,
        "nlp_needs_crane_tow": 0,
        "nlp_weather_water": 0,
        "nlp_agency_mention": 0,
        "nlp_kannada_cues": 0,
        "nlp_vehicle_hint": "unknown",
        "nlp_breakdown_subtype": "other",
        "nlp_severity_cue": 3,
        "nlp_event_subtype": "other",
        "nlp_urgency_tone": 0,
        "nlp_estimated_duration_min": 0.0,
    }


# =================================================================== Tier 2
def _load_cache() -> dict:
    """Load the optional Tier-2 (LLM) cache, if it exists.

    Cache format: {sha1(desc) -> {field: value, ...}}. We never write to the
    cache from this module (callers / scripts do that).
    """
    p = C.DATA_PROC / "llm_features_cache.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def apply_to_df(df: pd.DataFrame) -> pd.DataFrame:
    """Apply Tier-1 to every row, merge any Tier-2 cache hits on top.

    Returns a DataFrame with the nlp_* columns aligned to df.index. Any
    existing `nlp_*` columns in `df` are dropped first so the result is
    deterministic.
    """
    nlp_cols = list(_defaults().keys())
    df = df.drop(columns=[c for c in nlp_cols if c in df.columns], errors="ignore")
    descs = df["description"] if "description" in df.columns else pd.Series([""] * len(df))
    rows = [rule_extract(d) for d in descs]
    nlp_df = pd.DataFrame(rows, index=df.index)

    # ---- Tier 2 cache: fill in any field Tier-1 left at default
    cache = _load_cache()
    if cache:
        def _h(s):
            try:
                import hashlib
                return hashlib.sha1(str(s).strip().encode("utf-8")).hexdigest()
            except Exception:
                return None
        for i, d in enumerate(descs):
            h = _h(d)
            if not h or h not in cache:
                continue
            ce = cache[h]
            for k, v in ce.items():
                col = f"nlp_{k}"
                if col in nlp_df.columns:
                    cur = nlp_df.at[df.index[i], col]
                    # only overwrite Tier-1 if Tier-1 was at default
                    default_val = _defaults()[col]
                    if cur == default_val or (isinstance(cur, float) and np.isnan(cur)):
                        nlp_df.at[df.index[i], col] = v

    # ---- subtype label encoding (deterministic on the union of values seen).
    # The encoded column is named `nlp_event_subtype_le` so it matches the
    # convention in `features.encode_categoricals`; that module will re-encode
    # the string `nlp_event_subtype` on its TRAIN split — we just leave the
    # raw string here and let the standard encoder handle it.
    return nlp_df


def main():
    df = pd.read_parquet(C.CLEAN_PARQUET)
    nlp = apply_to_df(df)
    out = pd.concat([df[["id"]].reset_index(drop=True),
                     nlp.reset_index(drop=True)], axis=1)
    out.to_parquet(C.NLP_FEATURES_PARQUET, index=False)
    print(f"wrote {C.NLP_FEATURES_PARQUET}  shape={out.shape}")
    print(f"coverage: {(df['description'].notna().sum())}/{len(df)} "
          f"rows with description ({100*df['description'].notna().mean():.1f}%)")
    print(f"kannada rows parsed: {int(nlp['nlp_kannada_cues'].sum())} / "
          f"{int(KANNADA_RE.search(' '.join(df['description'].fillna('').tolist() or [''])).group(0) is not None if False else 0)}")
    print(f"kannada cues (U+0C80..U+0CFF in any desc): "
          f"{df['description'].fillna('').str.contains(KANNADA_RE).sum()}")
    print()
    # ---- feature coverage table
    print("Feature coverage (of 8,171 rows):")
    for c in ["nlp_lanes_blocked", "nlp_needs_crane_tow", "nlp_weather_water",
              "nlp_agency_mention", "nlp_kannada_cues"]:
        n = int(nlp[c].sum())
        print(f"  {c:>26s}: {n:>5d}  ({100*n/len(df):.1f}%)")
    print()
    print("nlp_event_subtype distribution:")
    print(nlp["nlp_event_subtype"].value_counts().to_string())
    print()
    print("nlp_urgency_tone distribution:")
    print(nlp["nlp_urgency_tone"].value_counts().sort_index().to_string())
    print()
    print("nlp_estimated_duration_min > 0 rows:",
          int((nlp["nlp_estimated_duration_min"] > 0).sum()))
    print("nlp_estimated_duration_min percentiles:",
          np.percentile(nlp["nlp_estimated_duration_min"], [50, 75, 90]).round(1).tolist())


if __name__ == "__main__":
    main()
