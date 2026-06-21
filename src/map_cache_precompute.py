"""GridLock — 07: Pre-compute the demo map cache (spec 07 §"Demo-safety").

Per spec 07 §"Demo-safety (critical)":
  "Pre-compute & cache the distance-matrix and diversion routes for
   the demo scenarios → demo must NOT depend on a live Mappls call."

This script populates `artifacts/map_cache/` with the routes +
distance matrices for every pair of corridors in the demo scenarios.
After this runs, the API serves every demo call from disk (no HTTP).

Usage:
  python -m src.map_cache_precompute
"""
from __future__ import annotations
import json
import sys
import time
from pathlib import Path

import numpy as np

from . import config as C
from .mappls_service import (build_diversion_route, distance_matrix,
                              DEFAULT_CORRIDOR_COORDS, route,
                              BENGALURU_CENTER, _cache_set, _cache_get,
                              _TOKEN, _fallback_dm)
from .mappls_service import CACHE_DIR
import requests


# Pre-computed diversion chains for the spec 03 demo scenarios + a
# few extras the judge might ask about.
DEMO_CORRIDOR_PAIRS = [
    # (origin, target) — pairs that show up in the spec 06 demo
    ("Mysore Road",       "Magadi Road"),
    ("Tumkur Road",       "West of Chord Road"),
    ("ORR East 1",        "Hosur Road"),
    ("Bellary Road 1",    "Old Madras Road"),
    ("Hosur Road",        "Bannerghata Road"),
    ("Bannerghata Road",  "Hosur Road"),
    ("ORR North 1",       "ORR North 2"),
    ("ORR East 1",        "ORR East 2"),
    ("CBD 1",             "CBD 2"),
    ("Magadi Road",       "Mysore Road"),
    ("Old Madras Road",   "Old Airport Road"),
    ("Old Airport Road",  "Old Madras Road"),
    ("Hennur Main Road",  "IRR(Thanisandra road)"),
    ("Varthur Road",      "ORR East 2"),
]


def precompute():
    print("=== GridLock 07: pre-compute map cache ===\n")
    t0 = time.time()
    n_routes = 0
    for origin, target in DEMO_CORRIDOR_PAIRS:
        r = build_diversion_route(origin, target)
        n_routes += 1
        print(f"  route {origin:>22s} → {target:<22s}  "
              f"{r['eta_min']:>5.1f} min  {r['km']:>5.1f} km  "
              f"polyline={len(r['polyline'])} pts  ({r['source']})")
    print(f"\n  pre-computed {n_routes} routes in {time.time() - t0:.1f}s")

    # ---- full corridor-to-corridor distance matrix (22x22)
    corridors = list(DEFAULT_CORRIDOR_COORDS.keys())
    origins = [{"lat": c[0], "lon": c[1]} for c in
                [DEFAULT_CORRIDOR_COORDS[c] for c in corridors]]
    dests = origins
    print(f"\n  computing 22×22 corridor distance matrix ...")
    t0 = time.time()
    # Call the live Mappls DM endpoint ONCE with origins=22, dest=22.
    # The endpoint accepts up to ~50 points per call, so 22x22 is fine.
    if _TOKEN.has_credentials:
        try:
            origin_str = ";".join(f"{o['lon']},{o['lat']}" for o in origins)
            dest_str = ";".join(f"{d['lon']},{d['lat']}" for d in dests)
            sources = ";".join(str(i) for i in range(len(origins)))
            targets = ";".join(str(len(origins) + i) for i in range(len(dests)))
            url = (f"https://route.mappls.com/route/dm/distance_matrix/driving/"
                   f"{origin_str};{dest_str}?sources={sources}&destinations={targets}"
                   f"&access_token={_TOKEN.get()}")
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            data = r.json()
            dm = {"durations": data.get("results", {}).get("durations", []),
                  "distances": data.get("results", {}).get("distances", []),
                  "source": "mappls"}
            print(f"  done in {time.time() - t0:.1f}s  "
                  f"(source={dm['source']}, n×n={len(dm['durations'])}×{len(dm['durations'][0]) if dm['durations'] else 0})")
        except Exception as exc:
            print(f"  live DM failed ({exc}), falling back to haversine")
            dm = _fallback_dm(origins, dests)
            print(f"  done in {time.time() - t0:.1f}s  "
                  f"(source={dm['source']}, n×n={len(dm['durations'])}×{len(dm['durations'][0]) if dm['durations'] else 0})")
    else:
        dm = _fallback_dm(origins, dests)
        print(f"  done in {time.time() - t0:.1f}s  "
              f"(source={dm['source']}, n×n={len(dm['durations'])}×{len(dm['durations'][0]) if dm['durations'] else 0})")
    out = {
        "corridors": corridors,
        "durations_s": dm["durations"],
        "distances_m": dm["distances"],
        "source": dm["source"],
        "computed_at": time.time(),
    }
    (CACHE_DIR / "corridor_distance_matrix.json").write_text(
        json.dumps(out, default=str), encoding="utf-8")
    print(f"  -> {CACHE_DIR / 'corridor_distance_matrix.json'}")

    # ---- cache all 22 corridor coords so the API serves them
    coords = {}
    for c in corridors:
        lat, lon = DEFAULT_CORRIDOR_COORDS[c]
        coords[c] = {"lat": lat, "lon": lon, "name": c}
    (CACHE_DIR / "corridor_coords.json").write_text(
        json.dumps(coords, indent=2), encoding="utf-8")
    print(f"  -> {CACHE_DIR / 'corridor_coords.json'}")


if __name__ == "__main__":
    precompute()
