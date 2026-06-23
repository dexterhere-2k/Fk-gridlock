"""NexGen — 04: Pydantic v2 schemas for the FastAPI gateway.

One schema per endpoint (request + response). Designed to be the
single source of truth for the contract — `tests/test_api_contract.py`
asserts the live API matches these types.

Per spec 04 + 01/02/03 contracts:
  - every prediction carries an uncertainty band + `confidence` flag
  - `because` is the explainability payload (spec 08 #6)
  - NLP cues flow through as `nlp_cues` (spec 02)
"""
from __future__ import annotations
from datetime import datetime
from typing import Literal, Optional, Dict, List, Any
from pydantic import BaseModel, Field, ConfigDict


# ============================================================================
# Shared building blocks
# ============================================================================
class NLPCues(BaseModel):
    """Parsed free-text cues from spec 02 (Tier-1 or Tier-2 cache)."""
    model_config = ConfigDict(extra="forbid")
    lanes_blocked: bool = False
    needs_crane_tow: bool = False
    weather_water: bool = False
    agency_mention: bool = False
    kannada_cues: bool = False
    event_subtype: str = "unknown"
    urgency_tone: int = Field(0, ge=0, le=2)
    estimated_duration_min: float = Field(0.0, ge=0.0, le=24 * 60)


class CascadeEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")
    corridor: str
    lag_h: int
    lag_min: int
    r: float


# ============================================================================
# Request models
# ============================================================================
class ClearanceRiskRequest(BaseModel):
    """POST /api/clearance-risk — single incident prediction."""
    model_config = ConfigDict(extra="forbid")
    id: Optional[str] = Field(None, description="Optional client-side id (echoed back as prediction_id)")
    corridor: str = Field("Non-corridor", description="Bengaluru corridor name (e.g. 'Mysore Road')")
    zone: Optional[str] = Field("Unknown", description="Zone name")
    event_cause: str = Field("vehicle_breakdown", description="Cause category (e.g. tree_fall, accident)")
    veh_type: Optional[str] = Field("unknown", description="Vehicle type if applicable")
    police_station: Optional[str] = Field("Unknown", description="Police station jurisdiction")
    event_type: str = Field("unplanned", description="planned | unplanned")
    is_planned: bool = Field(False, description="True for planned events (rallies, VIP, etc.)")
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    requires_road_closure: bool = False
    description: Optional[str] = None
    nlp: Optional[NLPCues] = None
    datetime: Optional[str] = None
    start_datetime: Optional[str] = None


class SimulateRequest(BaseModel):
    """POST /api/simulate — planned-event impact forecast."""
    model_config = ConfigDict(extra="forbid")
    corridor: str
    event_cause: str
    zone: Optional[str] = "Unknown"
    veh_type: Optional[str] = "unknown"
    police_station: Optional[str] = "Unknown"
    is_planned: bool = True
    event_type: str = "planned"
    description: Optional[str] = None
    attendance: Optional[int] = Field(None, ge=0)
    datetime: Optional[str] = None


class OptimizeEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    corridor: str
    cause: str
    p50_min: float = Field(..., ge=0)
    p90_min: float = Field(..., ge=0)
    closure_prob: float = Field(0.0, ge=0.0, le=1.0)
    corridor_risk: float = Field(0.3, ge=0.0, le=1.0)
    is_planned: bool = False


class OptimizeUnit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    station: str = "Yeshwanthpura PS"
    agency: Literal["police", "traffic", "BBMP", "BESCOM", "BWSSB"] = "police"
    on_scene_event: Optional[str] = None


class OptimizeRequest(BaseModel):
    """POST /api/optimize — joint allocation across concurrent events."""
    model_config = ConfigDict(extra="forbid")
    events: list[OptimizeEvent] = Field(..., min_length=1, max_length=200)
    units: list[OptimizeUnit] = Field(..., min_length=1, max_length=2000)
    pool_cap: int = Field(200, ge=1, le=2000)
    lambda_cascade: float = Field(0.10, ge=0.0, le=10.0)
    lambda_switch: float = Field(0.3, ge=0.0, le=10.0)


class TransitionRequest(BaseModel):
    """POST /api/incident/{id}/transition — advance the state machine."""
    model_config = ConfigDict(extra="forbid")
    target_state: Literal["reported", "dispatched", "on_scene", "clearing", "closed"]


class OutcomeRequest(BaseModel):
    """POST /api/outcome — record actual vs predicted (feeds the learning loop)."""
    model_config = ConfigDict(extra="forbid")
    event_id: str = Field(..., description="Prediction/incident ID this outcome is for")
    actual_p50_min: Optional[float] = Field(None, ge=0)
    actual_p90_min: Optional[float] = Field(None, ge=0)
    actual_closure: Optional[bool] = None
    actual_officers_deployed: Optional[int] = Field(None, ge=0)
    actual_barricades: Optional[int] = Field(None, ge=0)
    notes: Optional[str] = None


class DispatchRequest(BaseModel):
    """POST /api/dispatch — nearest eligible unit."""
    model_config = ConfigDict(extra="forbid")
    incident_id: str


# ============================================================================
# Response models
# ============================================================================
class ClearanceRiskResponse(BaseModel):
    """The single-incident prediction contract (consumed by frontend view 1)."""
    p10: int = Field(..., ge=1)
    p50: int = Field(..., ge=1)
    p90: int = Field(..., ge=1)
    closure_prob: float = Field(..., ge=0.0, le=1.0)
    closure_tier: Literal["HIGH", "MED", "LOW"]
    closure_ml_prob: float = Field(..., ge=0.0, le=1.0)
    closure_lookup_rate: float = Field(..., ge=0.0, le=1.0)
    survival_median_min: float = Field(..., ge=0.0)
    corridor_risk: float = Field(..., ge=0.0, le=1.0)
    corridor: str
    cascade_downstream: list[CascadeEdge] = Field(default_factory=list)
    nlp_cues: NLPCues
    confidence: Literal["high", "medium", "low"]
    because: list[str] = Field(default_factory=list)
    prediction_id: Optional[str] = None  # server-assigned, for /api/explain lookup


class SimulateResponse(BaseModel):
    """Planned-event forecast — clearance + recommended plan."""
    model_config = ConfigDict(extra="forbid")
    prediction: ClearanceRiskResponse
    alert_level: Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"]
    plan: dict
    summary: str


class OptimizeEventAllocation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    officers: int
    officer_ids: list[str] = Field(default_factory=list)
    barricades: int
    diversion_route: Optional[str] = None
    pre_deploy_lead_time: int
    need: int
    understaffed_by: int
    because: list[str] = Field(default_factory=list)
    # spec 08 #6 — explainability `because` payload
    confidence: Optional[Literal["high", "medium", "low"]] = None
    comparator: Optional[dict] = None
    uncertainty: Optional[dict] = None


class OptimizePrePositioned(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str
    target: str
    lag_min: int


class OptimizeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str
    solve_time_s: float
    objective: float
    events: dict[str, OptimizeEventAllocation]
    summary: dict
    # spec 08 #6 — global explainability (ILP-solve meta + escalation nudge)
    global_because: list[str] = Field(default_factory=list)


class CorridorRisk(BaseModel):
    model_config = ConfigDict(extra="forbid")
    corridor: str
    events: int
    med_clear: float
    p90_clear: float
    closure_rate: float
    risk_score: float


class CorridorRiskList(BaseModel):
    model_config = ConfigDict(extra="forbid")
    n_corridors: int
    corridors: list[CorridorRisk]


class CascadeGraphResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    n_edges: int
    n_corridors: int
    n_hours: int
    trigger_rank: list[dict]
    # canonical: strongest_edges (used by frontend LiveView)
    strongest_edges: list[dict] = []
    # back-compat alias for any older client
    top_edges: list[dict] = []
    strongest_chains: list[str] = []


class CascadeDownstreamResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    corridor: str
    downstream: list[CascadeEdge]
    n_downstream: int


class IncidentReport(BaseModel):
    """POST /api/incidents — operator's quick incident report.

    Operator only supplies corridor + cause + free-text note. The backend
    auto-calls the predictor (01) to fill in p50/p90/closure_prob, and
    uses spec 02 NLP to extract cues from the note (Kannada supported).
    Auto-fires the cascade pre-alert (spec 08 #7) and assigns SLA per
    cause (spec 08 #2).
    """
    model_config = ConfigDict(extra="forbid")
    id: Optional[str] = None
    corridor: str
    cause: str
    is_planned: bool = False
    operator_note: Optional[str] = None
    veh_type: Optional[str] = None
    cargo_material: Optional[str] = None


class IncidentState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    corridor: str
    cause: str
    state: str
    reported_at: str
    last_transition_at: str
    sla_minutes: int
    elapsed_minutes: float
    sla_breached: bool
    priority: Literal["High", "Low"]
    # spec 08 #2 — auto-escalation telemetry
    sla_breaches: int = 0
    escalation_reason: Optional[str] = None
    # spec 08 #7 — cascade pre-alert (fire on report, on verify)
    cascade_alerts: list[dict] = []
    # spec 01 — predictor output attached at report time
    prediction: Optional[dict] = None


class IncidentListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    n_active: int
    incidents: list[IncidentState]


class DispatchAlternative(BaseModel):
    model_config = ConfigDict(extra="forbid")
    unit_id: str
    station: str
    agency: str
    eta_min: float
    eta_source: str


class DispatchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    incident_id: str
    incident_corridor: str
    incident_cause: str
    preferred_agency: str
    dispatched_unit: str
    dispatched_unit_station: Optional[str] = None
    dispatched_unit_agency: str
    estimated_eta_min: Optional[float] = None
    eta_source: str  # "matrix" | "haversine" | "unknown"
    agency_match: Literal["exact", "police_fallback", "mismatch"]
    confidence: Literal["high", "medium", "low"]
    eligible_count: int
    alternatives: list[DispatchAlternative] = []
    because: list[str] = []
    rationale: str


class ScheduleSlot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    time_offset_min: int
    action: str
    units: int
    reason: str


class ScheduleResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: str
    start_at: str
    slots: list[ScheduleSlot]
    summary: str
    diversion_route_geo: Optional[dict] = None  # spec 07 — polyline + eta


class ExplainResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recommendation_id: str
    because: list[str]
    confidence: Literal["high", "medium", "low"]
    contributing_features: list[dict] = Field(default_factory=list)


class AccuracyPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: str
    predicted_p50: float
    actual_p50: float
    predicted_closure: bool
    actual_closure: bool
    error_min: float


class AccuracyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    n_outcomes: int
    p50_mae_min: float
    closure_accuracy: float
    points: list[AccuracyPoint] = Field(default_factory=list)


class LearningSignalResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    signal: dict
    last_run: Optional[dict] = None
    retrain_triggered: bool
    trigger_reason: str


class RetrainResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str
    elapsed_s: Optional[float] = None
    stages: Optional[list[str]] = None
    stage: Optional[str] = None
    stderr: Optional[str] = None


# ---- spec 07 — Mappls/MapmyIndia map endpoints
class DiversionRequest(BaseModel):
    """POST /api/map/diversion — compute a route between two corridors."""
    model_config = ConfigDict(extra="forbid")
    origin_corridor: str
    target_corridor: str


class EtaRequest(BaseModel):
    """POST /api/map/eta — origin→dest travel time via the Mappls DM."""
    model_config = ConfigDict(extra="forbid")
    origin_lat: float
    origin_lon: float
    dest_lat: float
    dest_lon: float


class NearestStationRequest(BaseModel):
    """POST /api/map/nearest-station — find the nearest police station."""
    model_config = ConfigDict(extra="forbid")
    lat: float
    lon: float
    radius_m: int = 20000


class GeoJsonResponse(BaseModel):
    """Generic GeoJSON FeatureCollection wrapper (any of the map layers)."""
    model_config = ConfigDict(extra="forbid")
    layer: str
    n_features: int
    source: str = "mappls|fallback|static"
    geojson: dict


class MapHealthResponse(BaseModel):
    """GET /api/map/health — service status + per-product coverage."""
    model_config = ConfigDict(extra="forbid")
    has_credentials: bool
    cache_entries: int
    cache_dir: str
    n_corridors: int
    n_police_stations: int
    coverage: dict
    limitations: list[str]


class DebriefResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: str
    plan: dict
    actual: dict
    variance: dict


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["ok", "degraded", "down"]
    n_artifacts_loaded: int
    n_corridors: int
    n_cascade_edges: int
    uptime_s: float
    version: str
