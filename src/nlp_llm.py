"""NexGen — 02: Tier-2 LLM enrichment (OPTIONAL one-time batch).

The rule-based Tier-1 extractor (`nlp_extract.py`) is the **default + only
thing that runs at demo time**. This script is a one-shot batch over the
~6,811 description rows using a hosted LLM (Groq free tier) or a local one
(Ollama). Its output is a JSON cache that the live backend reads via
`nlp_extract._load_cache()` — **no model is ever loaded at inference**.

Per spec 02 §2:
  - Privacy: the dataset is already anonymized (`[LOCATION]`, `[PERSON]`,
    `[ORG]` tokens), so a hosted call is acceptable. In production, swap to
    a local BTP-hosted model.
  - "At demo time NOTHING runs an LLM — the backend reads the committed cache."

Usage:
  GROQ_API_KEY=...   python -m src.nlp_llm                 # full batch
  GROQ_API_KEY=...   python -m src.nlp_llm --limit 5       # smoke test
  python -m src.nlp_llm --provider ollama --limit 5       # local Ollama

Output: data/llm_features_cache.json
  {sha1(desc) -> {lanes_blocked, needs_crane_tow, weather_water,
                  event_subtype, agency_mention, severity_cue,
                  estimated_duration_min, ...}}

Tier-1 already covers `lanes_blocked`, `needs_crane_tow`, `weather_water`,
`agency_mention`, `event_subtype`, `severity_cue` via rule-based. The LLM
adds **accuracy** (it can read a full sentence where regexes can only match
keywords) and the **estimated_duration_min** that Tier-1 can only pull from
explicit numbers.

The cache key is a sha1 of the (stripped) description. Same text = same key
= same cached answer across runs (idempotent + resume-safe).
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import sys
import time
import re
import concurrent.futures
from typing import Optional

from . import config as C

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

CACHE_PATH = C.DATA_PROC / "llm_features_cache.json"

SYSTEM_PROMPT = (
    "You are a Bengaluru traffic-incident analyst. You read a short officer's "
    "field note (English, transliterated Kannada/Kanglish, or Kannada script). "
    "Tokens like [LOCATION], [PERSON], [ORG] are anonymization placeholders "
    "— treat them as a place/person name. Extract structured facts. Respond "
    "with ONLY a JSON object."
)

USER_TEMPLATE = """Officer note: "{desc}"

Return a JSON object with exactly these keys (and only these):
- "lanes_blocked": true/false (does it imply one or more lanes / the road are blocked?)
- "needs_crane_tow": true/false (does it imply a tow truck / crane / vehicle recovery is needed?)
- "weather_water": true/false (does it mention water / rain / flooding / water logging?)
- "agency_mention": one of "" or "BBMP" | "BWSSB" | "BESCOM" | "POLICE" | "METRO" | "KSRTC" | "OTHER"
- "event_subtype": one of "vehicle_breakdown" | "collision" | "tree_or_debris" |
                   "waterlogging_or_pothole" | "construction_or_utility" |
                   "crowd_or_event" | "vip_or_procession" | "congestion_only" | "other"
- "severity_cue": integer 1..5 (1=minor/congestion, 3=notable obstruction, 5=road-blocked/severe)
- "estimated_duration_min": integer 0..1440 (stated clearance time, 0 if not stated)

JSON only, no explanation."""


SUBTYPE_VALUES = [
    "vehicle_breakdown", "collision", "tree_or_debris", "waterlogging_or_pothole",
    "construction_or_utility", "crowd_or_event", "vip_or_procession",
    "congestion_only", "other",
]
AGENCY_VALUES = ["", "BBMP", "BWSSB", "BESCOM", "POLICE", "METRO", "KSRTC", "OTHER"]


def _hash(desc: str) -> str:
    return hashlib.sha1(desc.strip().encode("utf-8")).hexdigest()


def _coerce(raw: dict) -> dict:
    out = {
        "lanes_blocked": bool(raw.get("lanes_blocked", False)),
        "needs_crane_tow": bool(raw.get("needs_crane_tow", False)),
        "weather_water": bool(raw.get("weather_water", False)),
        "agency_mention": str(raw.get("agency_mention", "") or "").strip().upper(),
        "event_subtype": str(raw.get("event_subtype", "other") or "other").strip().lower(),
        "severity_cue": 3,
        "estimated_duration_min": 0,
    }
    # snap to known values
    if out["event_subtype"] not in SUBTYPE_VALUES:
        out["event_subtype"] = "other"
    if out["agency_mention"] not in AGENCY_VALUES:
        out["agency_mention"] = "OTHER" if out["agency_mention"] else ""
    try:
        out["severity_cue"] = min(5, max(1, int(round(float(raw.get("severity_cue", 3))))))
    except Exception:
        pass
    try:
        out["estimated_duration_min"] = min(1440, max(0, int(round(float(raw.get("estimated_duration_min", 0))))))
    except Exception:
        pass
    return out


# ---------------------------------------------------------------- Groq backend
def _call_groq(desc: str, model: str = "llama-3.1-8b-instant",
               timeout: float = 30.0) -> Optional[dict]:
    import requests
    api_key = os.environ.get("GROQ_API_KEY", "").strip()
    if not api_key:
        return None
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}",
               "Content-Type": "application/json"}
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": USER_TEMPLATE.format(desc=desc[:800])},
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    try:
        r = requests.post(url, headers=headers, json=body, timeout=timeout)
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
        return json.loads(content)
    except Exception as e:
        print(f"  groq error: {e}", file=sys.stderr)
        return None


def _call_ollama(desc: str, model: str = "qwen2.5:3b",
                 timeout: float = 60.0) -> Optional[dict]:
    import requests
    url = "http://localhost:11434/api/chat"
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": USER_TEMPLATE.format(desc=desc[:800])},
        ],
        "stream": False,
        "format": "json",
    }
    try:
        r = requests.post(url, json=body, timeout=timeout)
        r.raise_for_status()
        content = r.json()["message"]["content"]
        return json.loads(content)
    except Exception as e:
        print(f"  ollama error: {e}", file=sys.stderr)
        return None


# ---------------------------------------------------------------- main batch
def run(limit: Optional[int] = None, provider: str = "groq",
        workers: int = 4, model: Optional[str] = None) -> None:
    """Run the Tier-2 batch. Writes/updates `data/llm_features_cache.json`.

    Idempotent: rows already in the cache are skipped.
    """
    import pandas as pd
    df = pd.read_parquet(C.CLEAN_PARQUET)
    descs = df["description"].fillna("").tolist()
    if limit:
        descs = descs[:limit]
    print(f"Tier-2 NLP batch: provider={provider} rows={len(descs)} workers={workers}")

    cache: dict = {}
    if CACHE_PATH.exists():
        try:
            cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            print(f"  cache loaded: {len(cache)} existing entries")
        except Exception:
            cache = {}

    # filter to rows that aren't already cached
    work = []
    for i, d in enumerate(descs):
        if not isinstance(d, str) or not d.strip():
            continue
        h = _hash(d)
        if h in cache:
            continue
        work.append((i, h, d))
    print(f"  to process: {len(work)} (skipped {len(descs) - len(work)} already-cached)")

    if not work:
        print("  nothing to do.")
        return

    call = _call_groq if provider == "groq" else _call_ollama
    if provider == "groq" and not os.environ.get("GROQ_API_KEY"):
        print("  GROQ_API_KEY not set; cannot run. (Set it and re-run, or use --provider ollama.)")
        return

    t0 = time.time()
    saved = 0
    errors = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(call, d, model=model) if model else ex.submit(call, d): (i, h)
                for (i, h, d) in work}
        done = 0
        for fut in concurrent.futures.as_completed(futs):
            i, h = futs[fut]
            raw = fut.result()
            done += 1
            if raw is None:
                errors += 1
                continue
            try:
                cache[h] = _coerce(raw)
                saved += 1
            except Exception:
                errors += 1
            if done % 50 == 0 or done == len(work):
                rate = done / max(1, time.time() - t0)
                print(f"  {done:>5d}/{len(work)}  rate={rate:.1f}/s  errors={errors}",
                      file=sys.stderr)

    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    print(f"\n  -> {CACHE_PATH}")
    print(f"  saved={saved}  errors={errors}  total_cache={len(cache)}")
    print(f"  elapsed={time.time()-t0:.1f}s")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--limit", type=int, default=None, help="cap on rows (smoke test)")
    p.add_argument("--provider", choices=["groq", "ollama"], default="groq")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--model", default=None,
                   help="LLM model id (default: llama-3.1-8b-instant for groq, "
                        "qwen2.5:3b for ollama)")
    args = p.parse_args()
    run(limit=args.limit, provider=args.provider,
        workers=args.workers, model=args.model)


if __name__ == "__main__":
    main()
