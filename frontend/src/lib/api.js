// Thin fetch wrapper for the FastAPI gateway. Every call returns a
// parsed JSON body and throws a typed `ApiError` on non-2xx, so the
// React layer can render the failure mode uniformly.

const BASE = ""; // same-origin via vite dev proxy

export class ApiError extends Error {
  constructor(message, status, body) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

async function request(path, init = {}) {
  const url = `${BASE}${path}`;
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json", ...(init.headers || {}) },
    ...init,
  });
  const text = await res.text();
  let body = null;
  if (text) {
    try { body = JSON.parse(text); } catch { body = text; }
  }
  if (!res.ok) {
    const detail = (body && body.detail) || res.statusText;
    throw new ApiError(`API ${res.status}: ${detail}`, res.status, body);
  }
  return body;
}

export const api = {
  health: () => request("/api/health"),

  // predict (01)
  clearanceRisk: (event) =>
    request("/api/clearance-risk", { method: "POST", body: JSON.stringify(event) }),
  simulate: (event) =>
    request("/api/simulate", { method: "POST", body: JSON.stringify(event) }),

  // optimize (03)
  optimize: (payload) =>
    request("/api/optimize", { method: "POST", body: JSON.stringify(payload) }),
  optimizeFromPredictions: (events, n_units = 20) =>
    request("/api/optimize/from-predictions", {
      method: "POST",
      body: JSON.stringify(events),
      headers: { "X-Gridlock-N-Units": String(n_units) },
    }),

  // corridor risk (01)
  riskCorridors: () => request("/api/risk/corridors"),

  // cascade (01 T4b)
  cascadeGraph: () => request("/api/cascade"),
  cascadeDownstream: (corridor) =>
    request(`/api/cascade/${encodeURIComponent(corridor)}`),
  // spec 08 #7 — proactive cascade pre-alert with urgency bucket
  // and risk multiplier the optimizer can use
  cascadeAlerts: (corridor) =>
    request(`/api/cascade/alerts/${encodeURIComponent(corridor)}`),

  // map (07 — Mappls)
  mapHealth: () => request("/api/map/health"),
  mapRiskHeatmap: () => request("/api/map/risk-heatmap"),
  mapIncidents: (limit = 500) =>
    request(`/api/map/incidents?limit=${limit}`),
  mapStations: () => request("/api/map/stations"),
  mapDiversion: (origin_corridor, target_corridor) =>
    request("/api/map/diversion", {
      method: "POST", body: JSON.stringify({ origin_corridor, target_corridor }),
    }),
  mapEta: (origin_lat, origin_lon, dest_lat, dest_lon) =>
    request("/api/map/eta", {
      method: "POST",
      body: JSON.stringify({ origin_lat, origin_lon, dest_lat, dest_lon }),
    }),
  mapNearestStation: (lat, lon, radius_m = 20000) =>
    request("/api/map/nearest-station", {
      method: "POST",
      body: JSON.stringify({ lat, lon, radius_m }),
    }),

  // incidents (08)
  listActiveIncidents: () => request("/api/incidents/active"),
  reportIncident: (event) =>
    request("/api/incidents", { method: "POST", body: JSON.stringify(event) }),
  transitionIncident: (id, target_state) =>
    request(`/api/incident/${encodeURIComponent(id)}/transition`, {
      method: "POST",
      body: JSON.stringify({ target_state }),
    }),
  dispatch: (incident_id) =>
    request("/api/dispatch", {
      method: "POST",
      body: JSON.stringify({ incident_id }),
    }),
  schedule: (planned_event_id) =>
    request(`/api/schedule/${encodeURIComponent(planned_event_id)}`),
  explain: (prediction_id) =>
    request(`/api/explain/${encodeURIComponent(prediction_id)}`),

  // learning loop (06/08)
  recordOutcome: (outcome) =>
    request("/api/outcome", { method: "POST", body: JSON.stringify(outcome) }),
  accuracy: () => request("/api/accuracy"),
  learningSignal: () => request("/api/learning/signal"),
  retrain: () =>
    request("/api/learning/retrain", { method: "POST" }),
  debrief: (event_id) =>
    request(`/api/debrief/${encodeURIComponent(event_id)}`),
};

export default api;
