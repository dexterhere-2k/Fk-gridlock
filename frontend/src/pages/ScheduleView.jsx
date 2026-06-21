// View 4 — Planned-event mode: pre-deployment timeline + diversion
// (Mappls route comes in spec 07; here we render the time-phased
// schedule from /api/schedule/{id}).

import { useEffect, useState } from "react";
import { api } from "../lib/api.js";
import {
  ErrorPanel, Loading, PageHeader, MetricCard,
} from "../components/Shared.jsx";
import MapplsMap from "../components/MapplsMap.jsx";

const PRESETS = [
  { id: "PLANNED-1", label: "Mysore Road VIP convoy",
    cause: "vip_movement", corridor: "Mysore Road" },
  { id: "PLANNED-2", label: "ORR East 1 marathon",
    cause: "public_event", corridor: "ORR East 1" },
  { id: "PLANNED-3", label: "Tumkur Road procession",
    cause: "procession", corridor: "Tumkur Road" },
];

export default function ScheduleView() {
  const [eventId, setEventId] = useState(PRESETS[0].id);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);
  const [corridors, setCorridors] = useState([]);
  const [stations, setStations] = useState([]);

  // Load map layers once
  useEffect(() => {
    let alive = true;
    Promise.all([api.riskCorridors(), api.mapStations()])
      .then(([rc, ms]) => {
        if (!alive) return;
        setCorridors(rc.corridors || []);
        setStations((ms.geojson?.features || []).map((f) => f.properties));
      })
      .catch(() => {});
    return () => { alive = false; };
  }, []);

  const run = async () => {
    setLoading(true); setError(null); setResult(null);
    try {
      const r = await api.schedule(eventId);
      setResult(r);
    } catch (e) { setError(e); }
    finally { setLoading(false); }
  };

  return (
    <div data-tour="schedule" className="space-y-4">
      <PageHeader
        title="Planned-event timeline"
        subtitle="Time-phased deployment: barricades T-120, officers T-30, demob at P50"
      />
      <ErrorPanel error={error} />

      <section className="card p-4">
        <h2 className="mb-2 text-sm font-semibold text-ink-100">Pick a planned event</h2>
        <div className="flex flex-wrap gap-1.5">
          {PRESETS.map((p) => (
            <button
              key={p.id}
              onClick={() => setEventId(p.id)}
              className={
                "rounded-full border px-2.5 py-1 text-[11px] " +
                (eventId === p.id
                  ? "border-accent-500 bg-accent-500/10 text-accent-400"
                  : "border-ink-700 bg-ink-900 text-ink-200 hover:bg-ink-800")
              }
            >
              {p.label}
            </button>
          ))}
        </div>
        <div className="mt-3">
          <button className="btn-primary" onClick={run} disabled={loading}>
            {loading ? "Building…" : "Build schedule"}
          </button>
        </div>
      </section>

      {loading && <Loading label="…" />}
      {result && <Timeline result={result} corridors={corridors} stations={stations} />}
    </div>
  );
}

function Timeline({ result, corridors, stations }) {
  const minT = Math.min(...result.slots.map((s) => s.time_offset_min));
  const maxT = Math.max(...result.slots.map((s) => s.time_offset_min));
  const range = Math.max(1, maxT - minT);
  // spec 07 — render the diversion route from the API on a small map
  const div = result.diversion_route_geo;
  return (
    <section className="card p-4">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-ink-100">Schedule: {result.event_id}</h2>
        <span className="label">{new Date(result.start_at).toLocaleString()}</span>
      </div>

      <div className="relative h-32 rounded-lg border border-ink-800 bg-ink-900/40">
        {/* axis */}
        <div className="absolute inset-x-0 top-1/2 h-px bg-ink-700" />
        <div className="absolute inset-y-0 left-1/2 w-px bg-accent-500/40" />
        <div className="absolute right-2 top-1 text-[10px] text-ink-400">T = event start</div>
        {result.slots.map((s, i) => {
          const x = ((s.time_offset_min - minT) / range) * 100;
          const isEvent = s.time_offset_min === 0;
          return (
            <div key={i} className="absolute -translate-x-1/2" style={{ left: `${x}%`, top: isEvent ? "40%" : "20%" }}>
              <div className={
                "h-3 w-3 rounded-full border-2 border-ink-950 " +
                (isEvent ? "bg-accent-500" : s.units > 0 ? "bg-warn-500" : "bg-ink-500")
              } />
              <div className="mt-1 w-32 -translate-x-1/2 absolute left-1/2 text-center text-[10px] text-ink-200">
                <div className="font-medium">T{s.time_offset_min > 0 ? "+" : ""}{s.time_offset_min}m</div>
                <div className="text-ink-400">{s.action.replace(/_/g, " ")}</div>
                {s.units > 0 && <div className="num text-accent-400">×{s.units}</div>}
              </div>
            </div>
          );
        })}
      </div>

      <div className="mt-12 space-y-2">
        {result.slots.map((s, i) => (
          <div key={i} className="card-tight flex items-center gap-3 px-3 py-2 text-sm">
            <span className="num w-12 text-ink-400">T{s.time_offset_min > 0 ? "+" : ""}{s.time_offset_min}m</span>
            <span className="text-ink-100">{s.action.replace(/_/g, " ")}</span>
            {s.units > 0 && <span className="pill-accent">×{s.units}</span>}
            <span className="ml-auto text-[11px] text-ink-400">{s.reason}</span>
          </div>
        ))}
      </div>

      {/* spec 07 — diversion polyline from the live Mappls DM endpoint */}
      {div && div.polyline && div.polyline.length > 1 && (
        <div className="mt-4">
          <div className="mb-1 flex items-center justify-between">
            <h3 className="text-sm font-semibold text-ink-100">Diversion route (Mappls live)</h3>
            <span className="text-[10px] text-ink-400">
              {div.eta_min} min  ·  {div.km} km  ·  {div.source}
            </span>
          </div>
          <MapplsMap
            corridors={corridors || []}
            stations={stations || []}
            diversions={[{
              id: result.event_id,
              source: "Mysore Road",
              target: "Magadi Road",
              eta_min: div.eta_min,
              km: div.km,
              polyline: div.polyline,
              source_kind: div.source === "mappls" ? "mappls" : "fallback_haversine_polyline",
            }]}
            width={720}
            height={320}
          />
        </div>
      )}

      <p className="mt-3 text-[11px] text-ink-500">{result.summary}</p>
    </section>
  );
}
