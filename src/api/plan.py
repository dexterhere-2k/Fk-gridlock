"""Single-screen scenario simulator endpoints.

These three endpoints power the /plan command-center tab on the frontend:

  GET  /api/plan/venues              -- curated Bengaluru venues
  POST /api/plan/simulate            -- physics-grounded forecast + plan
                                         (clearly labeled synthetic; uses our
                                         real 22-corridor coords for geography
                                         but a distance-decay generator for the
                                         impact numbers — does NOT touch the
                                         trained ML models from 01/02/03)
  GET  /api/replay/timeline          -- hourly historical incident counts for
                                         the Live-view Recharts timeline

The synthetic nature is intentional and disclosed: this is a simulator for
the demo, not a prediction. Real per-incident predictions still flow
through /api/clearance-risk (which uses the trained CoxPH + GBR + closure
classifier + cascade).
"""
from __future__ import annotations
import json
import math
import time
import urllib.request as _urllib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, ConfigDict

# Curated 8 Bengaluru venues with real lat/lng (matches our station coords)
# and capacity estimates drawn from public figures.
VENUES: List[Dict[str, Any]] = [
    {
        "id": "blr-chinnaswamy",
        "name": "Chinnaswamy Cricket Ground",
        "lat": 12.9789, "lng": 77.5996,
        "capacity": 38000,
        "base_radius_km": 3.5,
        "typicalEvents": ["cricket", "concert"],
    },
    {
        "id": "blr-palace",
        "name": "Bengaluru Palace",
        "lat": 12.9986, "lng": 77.5921,
        "capacity": 30000,
        "base_radius_km": 2.8,
        "typicalEvents": ["concert", "exhibition"],
    },
    {
        "id": "blr-freedom",
        "name": "Freedom Park Grounds",
        "lat": 12.9826, "lng": 77.5912,
        "capacity": 15000,
        "base_radius_km": 2.0,
        "typicalEvents": ["rally", "protest"],
    },
    {
        "id": "blr-kanteerava",
        "name": "Kanteerava Sports Complex",
        "lat": 12.9698, "lng": 77.5912,
        "capacity": 18000,
        "base_radius_km": 2.3,
        "typicalEvents": ["football", "athletics"],
    },
    {
        "id": "blr-vidhana",
        "name": "Vidhana Soudha Grounds",
        "lat": 12.9794, "lng": 77.5912,
        "capacity": 10000,
        "base_radius_km": 1.8,
        "typicalEvents": ["rally", "vip_movement"],
    },
    {
        "id": "blr-whitefield",
        "name": "Whitefield Tech Park",
        "lat": 12.9698, "lng": 77.7500,
        "capacity": 25000,
        "base_radius_km": 3.2,
        "typicalEvents": ["tech_event", "public_event"],
    },
    {
        "id": "blr-mgroad",
        "name": "MG Road District",
        "lat": 12.9756, "lng": 77.6063,
        "capacity": 25000,
        "base_radius_km": 2.4,
        "typicalEvents": ["procession", "public_event"],
    },
    {
        "id": "blr-cubbon",
        "name": "Cubbon Park Green Zone",
        "lat": 12.9763, "lng": 77.5929,
        "capacity": 22000,
        "base_radius_km": 2.6,
        "typicalEvents": ["marathon", "public_event"],
    },
]

EVENT_TYPES: Dict[str, Tuple[str, float]] = {
    # key -> (label, attendance_to_impact_factor)
    "cricket":      ("Cricket match",     1.10),
    "concert":      ("Concert",           1.20),
    "rally":        ("Political rally",   1.30),
    "protest":      ("Protest march",     1.25),
    "football":     ("Football match",    0.95),
    "athletics":    ("Athletics meet",    0.80),
    "marathon":     ("Marathon",          1.15),
    "procession":   ("Religious procession", 0.90),
    "vip_movement": ("VIP movement",      0.70),
    "tech_event":   ("Tech event",        0.75),
    "exhibition":   ("Exhibition",        0.60),
    "public_event": ("Public event",      0.85),
}

WEATHER_LABELS: Dict[int, str] = {
    0: "Clear", 1: "Light rain", 2: "Heavy rain",
}

DOW_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


# Curated gateway pairs for diversion routing (real Bengaluru OD pairs)
GATEWAY_PAIRS: List[Tuple[str, str]] = [
    ("chinnaswamy", "whitefield"),
    ("palace_grounds", "whitefield"),
    ("chinnaswamy", "mg_road"),
    ("freedom_park", "whitefield"),
    ("vidhana_soudha", "whitefield"),
    ("chinnaswamy", "cubbon_park"),
    ("kanteerava", "whitefield"),
    ("freedom_park", "mg_road"),
]


# ============================================================================
# Pydantic models
# ============================================================================
class PlanVenue(BaseModel):
    id: str
    name: str
    lat: float
    lng: float
    capacity: int
    base_radius_km: float
    typicalEvents: List[str]


class PlanSimulateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    venueId: str
    eventType: str
    attendance: int = Field(..., ge=100, le=200000)
    startHour: float = Field(..., ge=0, le=24)
    dow: int = Field(0, ge=0, le=6)
    isHoliday: bool = False
    rain: int = Field(0, ge=0, le=2)
    tempC: float = 28.0
    durationMin: int = Field(180, ge=30, le=720)
    manpowerBudget: int = Field(60, ge=0, le=200)


# ============================================================================
# Helpers
# ============================================================================
def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in km."""
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _arrival_curve(t_min: float, duration_min: float) -> float:
    """0..1 attendance-arrival factor (0 = start, 1 = peak)."""
    if t_min < 0:
        return 0.0
    if t_min < duration_min * 0.5:
        return t_min / (duration_min * 0.5)  # ramp up
    return max(0.0, 1.0 - (t_min - duration_min * 0.5) / (duration_min * 0.5))


def _baseline_congestion(hour: float, dow: int) -> float:
    """0..100 city-baseline congestion for a given hour and day-of-week."""
    h = hour % 24
    # Morning peak 8-10, evening peak 17-20. Use sin lobes.
    morn = math.exp(-((h - 9) ** 2) / 2.0)
    even = math.exp(-((h - 18.5) ** 2) / 3.0)
    base = 25 + 30 * max(morn, even)
    # Weekend softer peaks
    if dow >= 5:
        base = 18 + 22 * max(morn, even)
    return min(95.0, base)


def _rain_factor(rain: int) -> float:
    return {0: 1.0, 1: 1.15, 2: 1.35}[rain]


def _event_impact_factor(event_type: str) -> float:
    return EVENT_TYPES.get(event_type, ("unknown", 0.8))[1]


# ============================================================================
# Core forecast generator
# ============================================================================
def _build_22_corridor_graph() -> Dict[str, Tuple[float, float]]:
    """Return our 22 corridors as {id: (lat, lng)} for the synthetic planner."""
    try:
        from src.mappls_service import DEFAULT_CORRIDOR_COORDS
        # mappls_service stores (lat, lon) tuples
        return {k: v for k, v in DEFAULT_CORRIDOR_COORDS.items()}
    except Exception:
        # Fallback: Bengaluru center only
        return {"Bengaluru Center": (12.9716, 77.5946)}


def _per_corridor_impact(
    venue_id: str, attendance: int, event_type: str, rain: int,
    corridor_coords: Dict[str, Tuple[float, float]],
) -> Dict[str, Dict[str, float]]:
    """Compute the per-corridor predicted congestion at peak for a scenario.

    Uses a physics-grounded model:
        impact(c) = baseline(c, hour)
                  + event_impact_factor * (attendance / venue.capacity) ** 0.7
                  * exp(-dist / 4.0) * rain_factor
        delta(c)  = impact(c) - baseline(c, hour)
    """
    venue = next((v for v in VENUES if v["id"] == venue_id), VENUES[0])
    vlat, vlng = venue["lat"], venue["lng"]
    cap = venue["capacity"]
    impact_factor = _event_impact_factor(event_type)
    attendance_term = (attendance / cap) ** 0.7
    rf = _rain_factor(rain)

    per: Dict[str, Dict[str, float]] = {}
    for cid, (lat, lng) in corridor_coords.items():
        dist = _haversine_km(vlat, vlng, lat, lng)
        # Closeness to venue — exp decay with 4km e-folding
        closeness = math.exp(-dist / 4.0)
        # 0..100 surge at this corridor
        surge = 65.0 * impact_factor * attendance_term * closeness * rf
        # Cap surge to avoid silly >100 values
        surge = min(85.0, surge)
        per[cid] = {
            "lat": lat, "lng": lng,
            "congestion": round(min(100.0, 30 + surge * 0.4 + 10 * closeness), 1),
            "surge": round(surge, 1),
            "delta": round(surge, 1),
            "distance_km": round(dist, 2),
            "delay_min": round(1.0 + surge * 0.18, 1),
        }
    return per


def _timeline_buckets(
    venue_id: str, attendance: int, event_type: str, rain: int,
    duration_min: int, dow: int, start_hour: float,
    per_peak: Dict[str, Dict[str, float]],
    corridor_coords: Dict[str, Tuple[float, float]],
) -> Tuple[List[Dict[str, Any]], int]:
    """Build 30-min timeline buckets from T-60 to T+(duration+60)."""
    # Total minutes: pre-event 60 + duration + post 60
    pre, post = 60, 60
    total = pre + duration_min + post
    n = total // 30
    peak_idx = 0
    peak_avg = -1.0
    buckets = []
    for i in range(n):
        t_min = i * 30 - pre
        # Effective time-of-day for baseline
        eff_hour = (start_hour + t_min / 60.0) % 24
        baseline = _baseline_congestion(eff_hour, dow)
        # Arrival factor
        if t_min < 0:
            arr = 0.0
        elif t_min < duration_min * 0.5:
            arr = t_min / (duration_min * 0.5)
        else:
            arr = max(0.0, 1.0 - (t_min - duration_min * 0.5) / (duration_min * 0.5))
        # Per-corridor congestion at this time
        per_j: Dict[str, float] = {}
        deltas: Dict[str, float] = {}
        for cid, base in per_peak.items():
            surge = base["surge"] * arr
            per_j[cid] = round(min(100.0, baseline + surge * 0.5), 1)
            deltas[cid] = round(surge, 1)
        avg = sum(per_j.values()) / max(1, len(per_j))
        mx = max(per_j.values()) if per_j else 0.0
        if avg > peak_avg:
            peak_avg = avg
            peak_idx = i
        phase = ("arrival" if t_min < 0 else
                 "during" if t_min < duration_min else
                 "dispersal")
        # Clock-hour label for the X axis
        ch = int(eff_hour)
        cm = int((eff_hour - ch) * 60)
        label = f"{ch:02d}:{cm:02d}"
        buckets.append({
            "minutes": t_min,
            "label": label,
            "phase": phase,
            "clockHour": eff_hour,
            "avgCongestion": round(avg, 1),
            "maxCongestion": round(mx, 1),
            "totalDelay": round(sum(per_j.values()) * 0.05, 1),
            "junctionsAffected": sum(1 for v in per_j.values() if v >= 45),
            "congestion": per_j,
            "delta": deltas,
            "delay": {cid: round(1.0 + per_peak[cid]["surge"] * arr * 0.18, 1)
                       for cid in per_peak},
        })
    return buckets, peak_idx


def _kpis(per_peak: Dict[str, Dict[str, float]],
          timeline: List[Dict[str, Any]],
          peak_idx: int,
          venue_name: str,
          event_type: str) -> Dict[str, Any]:
    """Build the KPI summary tile for the dashboard top bar."""
    if not per_peak:
        return {
            "peakCongestion": 0, "peakTimeLabel": "—", "peakPhase": "—",
            "junctionsAffected": 0, "worstJunction": None,
            "impactRadiusKm": 0.0, "avgDelayAtPeak": 0.0, "totalDelayAtPeak": 0.0,
        }
    peak_bucket = timeline[peak_idx] if peak_idx < len(timeline) else timeline[0]
    # Worst corridor by surge
    worst = max(per_peak.items(), key=lambda kv: kv[1]["surge"])
    # Impact radius — distance to the worst corridor
    return {
        "peakCongestion": round(max(c["congestion"] for c in per_peak.values()), 1),
        "peakTimeLabel": peak_bucket["label"],
        "peakPhase": peak_bucket["phase"],
        "junctionsAffected": sum(1 for c in per_peak.values() if c["delta"] >= 10),
        "worstJunction": worst[0],
        "impactRadiusKm": round(worst[1]["distance_km"], 1),
        "avgDelayAtPeak": round(peak_bucket["avgCongestion"] * 0.12, 1),
        "totalDelayAtPeak": round(peak_bucket["totalDelay"], 1),
    }


# ============================================================================
# Plan: manpower / barricades / diversions
# ============================================================================
def _allocate_manpower(
    per_peak: Dict[str, Dict[str, float]],
    budget: int,
) -> Dict[str, Any]:
    """Greedy marginal-utility allocation."""
    RELIEF_DECAY = 0.80
    MAX_PER_JUNCTION = 12
    MAX_MITIGATION = 0.62
    AFFECTED_DELTA = 10.0

    affected = [(c, v) for c, v in per_peak.items() if v["delta"] >= AFFECTED_DELTA]
    affected.sort(key=lambda kv: kv[1]["delta"], reverse=True)
    if not affected:
        return {"officers": [], "totalDeployed": 0, "junctionsStaffed": 0}

    counts = {c: 0 for c, _ in affected}
    priority = {c: v["delta"] * 0.5 + v["congestion"] * 0.3
                for c, v in affected}

    for _ in range(budget):
        best_id, best_gain = None, 0.0
        for cid, _ in affected:
            if counts[cid] >= MAX_PER_JUNCTION:
                continue
            gain = priority[cid] * (RELIEF_DECAY ** counts[cid])
            if gain > best_gain:
                best_gain, best_id = gain, cid
        if best_id is None:
            break
        counts[best_id] += 1

    officers = []
    for cid, n in counts.items():
        if n == 0:
            continue
        v = per_peak[cid]
        mitigation = min(MAX_MITIGATION, 1 - RELIEF_DECAY ** n)
        officers.append({
            "junctionId": cid,
            "junctionName": cid,
            "lat": v["lat"], "lng": v["lng"],
            "officers": n,
            "priority": round(priority[cid], 3),
            "peakCongestion": v["congestion"],
            "eventDelta": v["delta"],
            "expectedDelayBefore": v["delay_min"],
            "expectedDelayAfter": round(v["delay_min"] * (1 - mitigation), 2),
            "mitigationPct": round(mitigation * 100, 0),
            "reason": f"+{v['delta']:.0f} pts event surge, "
                      f"{v['distance_km']:.1f}km from venue",
        })
    officers.sort(key=lambda o: o["officers"], reverse=True)
    return {
        "officers": officers,
        "totalDeployed": sum(o["officers"] for o in officers),
        "junctionsStaffed": len(officers),
    }


def _recommend_barricades(
    per_peak: Dict[str, Dict[str, float]],
    venue_lat: float, venue_lng: float,
    max_points: int = 5,
) -> List[Dict[str, Any]]:
    """Inflow-control points on the worst corridors near the venue."""
    candidates = []
    for cid, v in per_peak.items():
        if v["delta"] < 10:
            continue
        proximity = 1.0 / (1.0 + v["distance_km"])
        score = v["delta"] * (0.4 + 0.6 * proximity * 3)
        candidates.append((score, cid, v))
    candidates.sort(reverse=True)
    chosen = []
    used: Dict[str, int] = {}
    for score, cid, v in candidates:
        if len(chosen) >= max_points:
            break
        if used.get(cid, 0) >= 2:
            continue
        used[cid] = used.get(cid, 0) + 1
        action = ("Hard barricade + diversion" if v["delta"] >= 25
                  else "One-way / inflow metering")

        # Route the barricade polyline through OSRM for a real road-
        # following path from the corridor to the venue. Falls back to
        # straight line only if OSRM is unreachable.
        rpts, route_src, _ = _osrm_route(v["lat"], v["lng"], venue_lat, venue_lng)
        if not rpts:  # OSRM unreachable — skip this barricade
            continue

        chosen.append({
            "edge": f"{cid}__venue",
            "road": cid,
            "from": cid,
            "to": "venue approach",
            "route": [[lat, lng] for lat, lng in rpts],
            "routeSource": route_src,
            "action": action,
            "impact": round(v["delta"], 1),
            "distToVenueKm": round(v["distance_km"], 2),
            "reason": f"{cid} feeds venue (peak {v['congestion']:.0f}/100); "
                      f"restrict inflow.",
        })
    return chosen


# === OSRM road-following routing (cache + fallback) ============================
_OSRM_CACHE: Dict[Tuple[str, str], Any] = {}

def _osrm_route(
    olat: float, olng: float, dlat: float, dlng: float,
) -> Tuple[List[Tuple[float, float]], str, float]:
    """Call OSRM public API for a road-following route polyline.

    Returns (polyline points, source, distance_m).
    Falls back to a straight line if OSRM is unreachable.
    """
    cache_key = (f"{olat:.5f},{olng:.5f}", f"{dlat:.5f},{dlng:.5f}")
    if cache_key in _OSRM_CACHE:
        return _OSRM_CACHE[cache_key]

    try:
        url = (
            f"https://router.project-osrm.org/route/v1/driving/"
            f"{olng},{olat};{dlng},{dlat}"
            f"?overview=full&geometries=geojson"
        )
        req = _urllib.Request(url)
        req.add_header("Accept", "application/json")
        with _urllib.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())
        routes = data.get("routes", [])
        if routes:
            coords = routes[0]["geometry"]["coordinates"]
            # OSRM returns [lng, lat] → convert to [lat, lng]
            pts = [(c[1], c[0]) for c in coords]
            dist = routes[0].get("distance", 0.0)  # meters
            result = (pts, "osrm", round(dist))
            _OSRM_CACHE[cache_key] = result
            return result
    except Exception:
        pass

    # OSRM unreachable — no route available
    result = ([], "unavailable", 0)
    _OSRM_CACHE[cache_key] = result
    return result


def _osrm_route_alternatives(
    olat: float, olng: float, dlat: float, dlng: float,
) -> Tuple[List[List[Tuple[float, float]]], str, float, float]:
    """Call OSRM with alternatives=true to get multiple road-following routes.

    Returns (routes, source, fastest_duration_s, alternative_duration_s).
    Each route is a list of [lat, lng] waypoints following real roads.
    The first route is the fastest; the second is an alternative that
    takes different roads — this IS the diversion.

    Falls back to straight-line pairs if OSRM is unreachable.
    """
    cache_key = (f"alt:{olat:.5f},{olng:.5f}", f"{dlat:.5f},{dlng:.5f}")
    if cache_key in _OSRM_CACHE:
        cached = _OSRM_CACHE[cache_key]
        if isinstance(cached, tuple) and len(cached) == 4:
            return cached

    try:
        url = (
            f"https://router.project-osrm.org/route/v1/driving/"
            f"{olng},{olat};{dlng},{dlat}"
            f"?alternatives=true&geometries=geojson&overview=full&steps=false"
        )
        req = _urllib.Request(url)
        req.add_header("Accept", "application/json")
        with _urllib.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        routes = data.get("routes", [])
        if len(routes) >= 2:
            result_routes = []
            durations = []
            for r in routes[:2]:
                coords = r["geometry"]["coordinates"]
                pts = [(c[1], c[0]) for c in coords]
                result_routes.append(pts)
                durations.append(r.get("duration", 0.0))
            result = (result_routes, "osrm_alternatives",
                      durations[0], durations[1] if len(durations) > 1 else durations[0])
            _OSRM_CACHE[cache_key] = result
            return result
    except Exception:
        pass

    # OSRM unreachable — no alternatives available
    result = ([], "unavailable", 0.0, 0.0)
    _OSRM_CACHE[cache_key] = result
    return result


# === Diversion logic (OSRM alternatives) ======================================

def _recommend_diversions(
    per_peak: Dict[str, Dict[str, float]],
    corridor_coords: Dict[str, Tuple[float, float]],
    venue_lat: float, venue_lng: float,
    max_routes: int = 4,
) -> List[Dict[str, Any]]:
    """OSRM-based alternative-route diversions, routed from the venue.

    For each of the top-3 most congested corridors, compute OSRM routes
    from the VENUE to the farthest, least-congested corridor. The fastest
    OSRM route passes through/near the congested area; the alternative
    goes around it. Changing the venue changes all route endpoints.
    """
    by_surge = sorted(per_peak.items(), key=lambda kv: kv[1]["delta"], reverse=True)
    # Rank destinations by distance from venue + low congestion
    ranked_dests = []
    for dst_c, dv in per_peak.items():
        dist_km = _haversine_km(venue_lat, venue_lng, dv["lat"], dv["lng"])
        score = dist_km * (1.0 + (1.0 - dv["congestion"] / 100.0))
        ranked_dests.append((score, dst_c, dv))
    ranked_dests.sort(reverse=True)

    diversions = []
    seen = set()

    for _, dst_c, dv in ranked_dests:
        if len(diversions) >= max_routes:
            break
        if dst_c in ["Non-corridor"] or dv["delta"] >= 5:
            continue  # skip congested / dummy corridors
        if ("venue", dst_c) in seen:
            continue
        seen.add(("venue", dst_c))

        try:
            routes, src_label, dur0, dur1 = _osrm_route_alternatives(
                venue_lat, venue_lng, dv["lat"], dv["lng"],
            )
            if len(routes) < 2 or src_label == "unavailable":
                continue

            dur0_min = round(dur0 / 60.0, 1)
            dur1_min = round(dur1 / 60.0, 1)
            extra = round(dur1_min - dur0_min, 1)

            diversions.append({
                "from": "venue",
                "to": dst_c,
                "avoids": [c for c, v in by_surge[:3] if per_peak[c]["delta"] >= 10],
                "originalRoute": routes[0],
                "suggestedRoute": routes[1],
                "routeSource": src_label,
                "normalTimeMin": dur0_min,
                "divertedTimeMin": dur1_min,
                "extraDistanceMin": extra,
                "reason": (
                    f"Venue → {dst_c}: fastest route ({dur0_min} min) "
                    f"passes through the event zone; alternative avoids "
                    f"congested corridors and adds ~{extra} min."
                ),
            })
        except Exception:
            continue

    return diversions


# ============================================================================
# Router
# ============================================================================
def build_router() -> APIRouter:
    router = APIRouter(tags=["plan"])

    @router.get("/api/plan/venues", response_model=List[PlanVenue])
    def plan_venues() -> List[Dict[str, Any]]:
        return [{**v, "typicalEvents": list(v["typicalEvents"])} for v in VENUES]

    @router.get("/api/plan/event-types")
    def plan_event_types() -> List[Dict[str, str]]:
        return [{"key": k, "label": label} for k, (label, _) in EVENT_TYPES.items()]

    @router.get("/api/plan/weather-options")
    def plan_weather() -> List[Dict[str, Any]]:
        return [{"value": k, "label": v} for k, v in WEATHER_LABELS.items()]

    @router.post("/api/plan/simulate")
    def plan_simulate(req: PlanSimulateRequest) -> Dict[str, Any]:
        venue = next((v for v in VENUES if v["id"] == req.venueId), VENUES[0])
        corridor_coords = _build_22_corridor_graph()
        per_peak = _per_corridor_impact(
            venue_id=venue["id"],
            attendance=req.attendance,
            event_type=req.eventType,
            rain=req.rain,
            corridor_coords=corridor_coords,
        )
        timeline, peak_idx = _timeline_buckets(
            venue_id=venue["id"],
            attendance=req.attendance,
            event_type=req.eventType,
            rain=req.rain,
            duration_min=req.durationMin,
            dow=req.dow,
            start_hour=req.startHour,
            per_peak=per_peak,
            corridor_coords=corridor_coords,
        )
        kpis = _kpis(per_peak, timeline, peak_idx, venue["name"], req.eventType)
        # perJunction array for the map
        per_junction = [
            {
                "id": cid,
                "name": cid,
                "lat": v["lat"], "lng": v["lng"],
                "congestion": v["congestion"],
                "peakCongestion": v["congestion"],
                "baseline": round(v["congestion"] - v["delta"] * 0.4, 1),
                "delta": v["delta"],
                "delay": v["delay_min"],
                "peakMinutes": timeline[peak_idx]["minutes"] if peak_idx < len(timeline) else 0,
                "distanceKm": v["distance_km"],
                "centrality": 0.0,
            }
            for cid, v in sorted(per_peak.items(), key=lambda kv: kv[1]["delta"], reverse=True)
        ]
        event_label = EVENT_TYPES.get(req.eventType, ("unknown", 0.8))[0]
        forecast = {
            "event": {
                "venueId": venue["id"],
                "venueName": venue["name"],
                "venueLat": venue["lat"],
                "venueLng": venue["lng"],
                "venueCapacity": venue["capacity"],
                "eventType": req.eventType,
                "attendance": req.attendance,
                "attendanceRatio": round(req.attendance / venue["capacity"], 2),
                "startHour": req.startHour,
                "dow": req.dow,
                "isWeekend": req.dow >= 5,
                "isHoliday": req.isHoliday,
                "rain": req.rain,
                "tempC": req.tempC,
                "durationMin": req.durationMin,
            },
            "timeline": timeline,
            "peakIndex": peak_idx,
            "perJunction": per_junction,
            "kpis": kpis,
        }
        manpower = _allocate_manpower(per_peak, req.manpowerBudget)
        barricades = _recommend_barricades(per_peak, venue["lat"], venue["lng"])
        diversions = _recommend_diversions(per_peak, corridor_coords, venue["lat"], venue["lng"])
        plan = {
            "manpower": manpower,
            "barricades": barricades,
            "diversions": diversions,
            "summary": {
                "officersDeployed": manpower["totalDeployed"],
                "junctionsStaffed": manpower["junctionsStaffed"],
                "barricadePoints": len(barricades),
                "diversionRoutes": len(diversions),
                "manpowerBudget": req.manpowerBudget,
            },
        }
        return {
            "synthetic": True,
            "synthetic_note": ("This forecast is a physics-grounded simulator "
                              "(distance-decay + arrival/dispersal curve + "
                              "baseline + rain factor). It is NOT a prediction "
                              "from the trained ML models. Real per-incident "
                              "predictions flow through /api/clearance-risk."),
            "forecast": forecast,
            "plan": plan,
        }

    @router.get("/api/replay/timeline")
    def replay_timeline(hours: int = Query(24, ge=1, le=168)) -> Dict[str, Any]:
        """Hourly buckets of historical incident counts from the ASTraM log.

        Used by the Live-view Recharts timeline scrubber. We pull the
        historical start_datetime directly from the cleaned parquet
        (demo_replay emits pulses with the wall-clock `replay_ts`, not
        the historical timestamp, so we read the source data here).
        """
        from collections import defaultdict
        counts: Dict[int, int] = defaultdict(int)
        source = "synthetic_baseline"
        try:
            from src.config import CLEAN_PARQUET
            import pandas as pd
            df = pd.read_parquet(CLEAN_PARQUET)
            if "start_datetime" in df.columns:
                df = df.dropna(subset=["start_datetime"])
                hours_series = pd.to_datetime(df["start_datetime"]).dt.hour
                for h in hours_series:
                    counts[int(h)] += 1
                source = "astram_parquet"
        except Exception:
            pass
        if not counts:
            counts = {h: 30 + 80 * math.exp(-((h - 9) ** 2) / 4) + 90 * math.exp(-((h - 18) ** 2) / 4)
                       for h in range(24)}
        # Build dense 30-min buckets for the last `hours` window.
        # Buckets for the replay timeline
        # derived from incident density, n_incidents per bucket.
        now_hour = time.localtime().tm_hour
        max_count = max(counts.values()) if counts else 1
        buckets = []
        for i in range(hours * 2):
            h = (now_hour - hours + i // 2) % 24
            m = (i % 2) * 30
            v = counts.get(h, 0)
            # Split into two 30-min halves
            v_lo = v // 2
            v_hi = v - v_lo
            n = v_lo if m == 0 else v_hi
            density = n / max(1, max_count)
            buckets.append({
                "minutes": -((hours * 2 - i) * 30),
                "label": f"{h:02d}:{m:02d}",
                "phase": "history",
                "clockHour": h + m / 60.0,
                "avgCongestion": round(20 + density * 60, 1),
                "maxCongestion": round(30 + density * 60, 1),
                "totalDelay": round(n * 0.1, 1),
                "junctionsAffected": min(22, n // 5),
                "congestion": {},
                "delta": {},
                "delay": {},
                "n_incidents": n,
            })
        return {
            "hours": hours,
            "n_buckets": len(buckets),
            "buckets": buckets,
            "source": source,
        }

    return router
