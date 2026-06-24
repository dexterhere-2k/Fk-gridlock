"""NexGen — 07: Data-side geo helpers (corridor heatmap, incident pins).

Builds the GeoJSON the frontend renders in the MapplsMap component:

  - corridor_risk_geojson: 22 corridors as polygons-with-centers, colored
    by risk score (the heatmap layer)
  - incident_pins_geojson: a representative sample of historical
    incidents (limit 500) for the pins layer
  - police_station_geojson: the 10 closest police stations for the
    dispatch layer

This is the data bridge between the spec 01/02 outputs and the
Mappls map. It does NOT make any Mappls HTTP calls — those go
through `src/mappls_service.py` (cached/static fallback).
"""
from __future__ import annotations
import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

from . import config as C
from .mappls_service import (DEFAULT_CORRIDOR_COORDS, DEFAULT_STATION_COORDS,
                              BENGALURU_CENTER, _haversine_km)

warnings.filterwarnings("ignore")
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

MAP_DIR = C.ARTIFACTS_DIR / "map_geojson"
MAP_DIR.mkdir(parents=True, exist_ok=True)


def _color_for_risk(score: float) -> str:
    """Map a 0..1 risk score to a color (red→amber→green)."""
    if score >= 0.6:
        return "#dc2626"  # red-600
    if score >= 0.3:
        return "#f59e0b"  # amber-500
    return "#16a34a"  # green-500


def build_corridor_heatmap() -> dict:
    """GeoJSON FeatureCollection: 22 corridors as points (the frontend
    renders them as colored tiles). Includes the risk score and the
    cascade top-children for arrow rendering."""
    cr = pd.read_csv(C.CORRIDOR_RISK_CSV)
    cascade = pd.read_csv(C.CASCADE_EDGES_CSV)
    # build a quick lookup: corridor -> top 3 cascade children
    top_children: dict[str, list[dict]] = {}
    for src, grp in cascade.groupby("source"):
        top_children[src] = (
            grp.sort_values("r", ascending=False)
               .head(3)
               .to_dict(orient="records"))
    features = []
    for _, row in cr.iterrows():
        c = str(row["corridor"])
        lat, lon = DEFAULT_CORRIDOR_COORDS.get(c, BENGALURU_CENTER)
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point",
                           "coordinates": [lon, lat]},
            "properties": {
                "corridor": c,
                "events": int(row["events"]),
                "med_clear_min": round(float(row.get("med_clear") or 0), 1),
                "p90_clear_min": round(float(row.get("p90_clear") or 0), 1),
                "closure_rate": round(float(row.get("closure_rate") or 0), 4),
                "risk_score": round(float(row["risk_score"]), 3),
                "color": _color_for_risk(float(row["risk_score"])),
                "top_cascade_children": top_children.get(c, []),
            },
        })
    return {"type": "FeatureCollection", "features": features}


def build_incident_pins(limit: int = 500, seed: int = 42) -> dict:
    """GeoJSON: a sample of historical incidents as markers.

    Samples `limit` rows from the historical clean.parquet (deterministic
    via `seed`) so the map shows a stable representative view across
    reloads. Drops rows with missing coordinates.
    """
    if not C.CLEAN_PARQUET.exists():
        return {"type": "FeatureCollection", "features": []}
    df = pd.read_parquet(C.CLEAN_PARQUET)
    df = df.dropna(subset=["latitude", "longitude"])
    df = df[(df["latitude"] != 0) | (df["longitude"] != 0)]
    df = df.sample(n=min(limit, len(df)), random_state=seed)
    # pick a category for the icon (matches the front-end MapplsMap
    # category-icon mapping)
    def _icon(cause: str) -> str:
        c = (cause or "other").lower()
        if "tree" in c: return "tree_fall"
        if "vip" in c: return "vip"
        if "protest" in c: return "protest"
        if "water" in c or "logg" in c: return "water"
        if "construction" in c or "work" in c: return "construction"
        if "procession" in c: return "procession"
        if "public" in c or "event" in c: return "public_event"
        if "accident" in c: return "accident"
        if "breakdown" in c or "vehicle" in c: return "breakdown"
        if "pothole" in c or "pot_hole" in c: return "pothole"
        if "congestion" in c: return "congestion"
        return "other"
    features = []
    for _, r in df.iterrows():
        lat, lon = float(r["latitude"]), float(r["longitude"])
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {
                "id": str(r.get("id", "")),
                "corridor": str(r.get("corridor") or ""),
                "cause": str(r.get("event_cause") or ""),
                "icon": _icon(str(r.get("event_cause") or "")),
                "is_planned": bool(r.get("event_type") == "planned"),
                "closure": bool(r.get("requires_road_closure")),
                "start_datetime": str(r.get("start_datetime", "")),
            },
        })
    return {"type": "FeatureCollection", "features": features}


def build_police_stations(limit: int = 10) -> dict:
    """GeoJSON: the curated list of police stations (10 closest to
    Bengaluru center). In production this would call
    `mappls_service.nearby(...)` against the live ASTraM feed."""
    center = BENGALURU_CENTER
    items = []
    for name, (lat, lon) in DEFAULT_STATION_COORDS.items():
        d = _haversine_km(center[0], center[1], lat, lon)
        items.append({"name": name, "lat": lat, "lon": lon, "distance_km": round(d, 2)})
    items.sort(key=lambda r: r["distance_km"])
    items = items[:limit]
    return {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": {"type": "Point",
                           "coordinates": [s["lon"], s["lat"]]},
            "properties": {"name": s["name"], "distance_km": s["distance_km"]},
        } for s in items],
    }


def main():
    print("=== NexGen 07: Geo helpers ===\n")
    cr = build_corridor_heatmap()
    (MAP_DIR / "corridors.geojson").write_text(
        json.dumps(cr), encoding="utf-8")
    print(f"  corridors:  {len(cr['features'])} features  -> "
          f"corridors.geojson")

    pins = build_incident_pins(limit=500)
    (MAP_DIR / "incidents.geojson").write_text(
        json.dumps(pins), encoding="utf-8")
    print(f"  incidents:  {len(pins['features'])} features  -> "
          f"incidents.geojson")

    st = build_police_stations(limit=10)
    (MAP_DIR / "stations.geojson").write_text(
        json.dumps(st), encoding="utf-8")
    print(f"  stations:   {len(st['features'])} features  -> "
          f"stations.geojson")

    # ---- a quick summary
    print(f"\n  Bengaluru center: lat={BENGALURU_CENTER[0]}, lon={BENGALURU_CENTER[1]}")
    print(f"  corridors with hard-coded coords: "
          f"{len(DEFAULT_CORRIDOR_COORDS)}")
    print(f"  police stations: {len(DEFAULT_STATION_COORDS)}")


if __name__ == "__main__":
    main()
