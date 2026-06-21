"""Trilingual (English + Kannada + Kanglish) lexicon for Tier-1 NLP extraction.

The values are matched as lowercase substrings of the normalized `description`
text. Kannada script tokens are matched as-is; the Kanglish tokens are matched
case-insensitively in the latinized portion of the text. Hits are counted as
"any token matched" — used to flag presence / absence, not to weight.

Built from a 1-2h pass over the 870 Kannada-script rows + a top-frequency
scan of the 5,941 non-Kannada rows. The list is intentionally small and
explicit; we surface *cues*, not full NLU.
"""
from __future__ import annotations

# -----------------------------------------------------------------------------
# event_subtype categories (mutually exclusive — first match wins, in order)
# These are the 9 categories called out in spec 02 §3. Order matters:
# more specific categories come first.
SUBTYPE_KEYWORDS: list[tuple[str, list[str]]] = [
    ("vip_or_procession", [
        # English
        "vip", "procession", "rally", "dharna", "protest", "convoy",
        "minister", " cm ", "mla", "mp visit", "march", "bandh",
        # Kannada script
        "ಮೆರವಣಿಗೆ",  # procession
        "ಪ್ರತಿಭಟನೆ",  # protest
        "ಮುಖ್ಯಮಂತ್ರಿ",  # chief minister
        # Kanglish
        "meravanige", "pratibhanе", "sambhrama", "jatha", "bandh",
    ]),
    ("crowd_or_event", [
        "event", "function", "festival", "mela", "jatre", "jathre",
        "crowd", "gathering", "public event", "habba", "utsav", "fair",
        "cricket", "match", "concert", "rally", "ceremony",
        # Kannada
        "ಹಬ್ಬ", "ಉತ್ಸವ", "ಕಾರ್ಯಕ್ರಮ",  # festival, celebration, event
        # Kanglish
        "habba", "utsava", "kriket",
    ]),
    ("collision", [
        "accident", "collision", "collided", "hit ", "dashed", "dash",
        "crash", "overturn", "toppl", "rammed", "head on", "bumped",
        # Kannada
        "ಅಪಘಾತ",  # accident
        # Kanglish
        "apaghata",
    ]),
    ("tree_or_debris", [
        "tree fall", "treefall", "fallen tree", "log on road", "branch on road",
        "tree branch", "branch fell", "branch fallen", "tree fallen",
        "ಮರ ಬಿದ್ದಿದೆ",  # tree fallen (Kannada)
        "ಮರ ಬಿದ್ದ",  # tree fallen
        # Kanglish (use specific phrases to avoid matching city names)
        "mara bidide", "mara biddu", "mara patra", "male mara", "thorefalle",
    ]),
    ("waterlogging_or_pothole", [
        "water log", "waterlog", "water logging", "pothole",
        "pot hole", "flood", "gundi", "rain water", "drain over",
        "ನೀರು",  # water (Kannada)
        "ಗುಂಡಿ",  # pothole (Kannada)
        # Kanglish
        "neeru", "male", "gundi", "hosa",  # new (often "new pothole")
    ]),
    ("construction_or_utility", [
        "construction", "bwssb", "bescom", "nhai", "bbmp work",
        "digging", "pipe", "cable", "chamber", "road work",
        "joint cut", "barricad", "metro work", "under construction",
        "work in progress", "wip", "utility", "white topping", "whitetopping",
        "wt work", "asphalt", "ಡಾಂಬರ",  # asphalt (Kannada)
        "ವೈಟ್ ಟಾಪಿಂಗ್",  # white topping (Kannada transliteration)
        # Kanglish
        "ಬಿಬಿಎಂಪಿ", "ಬಿಇಎಸ್ಕಾಂ", "ಬಿಡಬ್ಲ್ಯೂಎಸ್ಎಸ್ಬಿ",
        "bibimpi", "bescom", "bwssb", "dambar", "wait topping",
    ]),
    ("vehicle_breakdown", [
        "breakdown", "break down", "break-down", "broke down", "broken",
        "puncture", "tyre puncture", "axle", "axel", "engine", "gearbox",
        "starting problem", "start problem", "stall", "off aag",
        "vehicle off", "dead", "fuel", "clutch", "diesel", "battery",
        "offroad", " off road", "off the road",
        # Kannada
        "ಆಫ್ ಆಗಿದೆ", "ಆಫ್ ರೋಡ್",  # off / off road
        "ಟೈರ್ ಪಂಚರ್",  # tyre puncture
        "ಬ್ರೇಕ್ ಡೌನ್",  # break down
        "ಸ್ಟಾರ್ಟಿಂಗ್ ಪ್ರಾಬ್ಲಮ್",  # starting problem
        # Kanglish
        "off aagide", "kettu nintide", "tayar bidide", "break down",
    ]),
    ("congestion_only", [
        "slow", "jam", "congest", "heavy traffic", "movement",
        "normal", "smooth", "peak", "rush", "traffic",
        # Kannada
        "ನಿಧಾನ",  # slow
        "ಟ್ರಾಫಿಕ್", "ಸಂಚಾರ",  # traffic, movement
        # Kanglish
        "nidhana", "samchara", "trafik", "jam", "slow movement",
    ]),
]

# -----------------------------------------------------------------------------
# Boolean cues — checked independently (not mutually exclusive)
BLOCK_TERMS: list[str] = [
    # English
    "block", "blocked", "closed", "close", "full block", "diversion", "divert",
    "stuck", "cannot pass", "cant pass", "single lane", "one lane", "no movement",
    "road closed", "stopped", "halt", "obstruct", "barricad",
    "ಒಂದು ಲೇನ್",  # one lane (Kannada)
    "ಬ್ಲಾಕ್", "ಬಂದ್",  # block, closed (Kannada)
    "ಅಡ್ಡಿ",  # obstruction (Kannada)
    # Kanglish
    "block aagide", "ondu lane", "full block", "road bandh", "band ide",
]

TOW_TERMS: list[str] = [
    "tow", "towing", "crane", "recovery", "lifting", "lift",
    "shift the veh", "shifted", "hydra", "pulled",
    "ಟೋ", "ಕ್ರೇನ್",  # tow, crane (Kannada)
    # Kanglish
    "crane beku", "tow beku", "ettuva", "lift maadi",
]

WATER_TERMS: list[str] = [
    "water", "rain", "water logging", "waterlog", "flood",
    "drain over", "ನೀರು", "ಮಳೆ",  # water, rain (Kannada)
    "neeru", "male", "ಮಳೆಗಾಲ",  # rainy season
]

AGENCY_TERMS: list[str] = [
    # English / acronyms
    "bbmp", "bwssb", "bescom", "metro", "police", "fire",
    "ksrtc", "ambulance", "bmrtc", "ksr", "ksrtc",
    "ಬಿಬಿಎಂಪಿ", "ಬಿಇಎಸ್ಕಾಂ", "ಬಿಡಬ್ಲ್ಯೂಎಸ್ಎಸ್ಬಿ",
    "ಪೊಲೀಸ್", "ಅಂಬುಲೆನ್ಸ್",
    # Kanglish
    "bibimpi", "bi dablyu", "bescom", "polisa",
]

VEHICLE_TERMS: list[str] = [
    "vehicle", "lorry", "truck", "bus", "car", "auto", "tempo", "lcv",
    "container", "tanker", "van", "bike", "two wheeler", "scooter", "cab",
    "taxi", "veh ", "bmtc",
    "ಬಸ್", "ಲಾರಿ", "ಕಾರು", "ಆಟೋ",  # bus, lorry, car, auto (Kannada)
    "bus", "lari", "kaaru", "auto",
]

SEVERE_TERMS: list[str] = [
    "road closed", "full block", "fully block", "overturn", "major", "severe",
    "huge", "big jam", "completely", "total block", "accident",
    "ತೀವ್ರ",  # severe (Kannada)
    "tivra", "ಭೀಕರ",  # terrible (Kannada)
]

CLEAR_TERMS: list[str] = [
    "cleared", "clear", "normal", "no issue", "no problem", "resolved",
    "smooth", "free flow", "ತೆರವು",  # clear (Kannada)
    "teravu", "ಸರಿ",  # okay
]

# -----------------------------------------------------------------------------
# Severity cue (ordinal 1..5) — explainability only, NEVER a training target.
# Mapped from the priority of cue matches:
#   1 = clear / no issue  ·  2 = trivial / congestion_only  ·  3 = default
#   4 = lanes_blocked or vip/event  ·  5 = severe terms
# (Severity ladder defined per spec 02 §NLP cues; no external heuristic.)
def severity_from_cues(subtype: str, blocking: bool, has_severe: bool,
                      has_clear: bool) -> int:
    sev = 3
    if subtype == "congestion_only":
        sev = 1
    if has_clear:
        sev = min(sev, 2)
    if blocking:
        sev = max(sev, 4)
    if has_severe:
        sev = 5
    if subtype in ("vip_or_procession", "crowd_or_event") and sev < 3:
        sev = 3
    return sev


# -----------------------------------------------------------------------------
# Subtype values used in the artifact (mirrors config.LLM_SUBTYPES from 01)
SUBTYPE_VALUES: list[str] = [
    "vehicle_breakdown", "collision", "tree_or_debris", "waterlogging_or_pothole",
    "construction_or_utility", "crowd_or_event", "vip_or_procession",
    "congestion_only", "other",
]


def normalize_subtype(s: str) -> str:
    """Snap to the canonical SUBTYPE_VALUES, defaulting to 'other'."""
    return s if s in SUBTYPE_VALUES else "other"
