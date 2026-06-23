// View 3 — Concurrent-events allocator (the wow feature, spec 05).
// Takes N events + M units, posts to /api/optimize, renders the
// allocation board + cascade pre-positioned corridors.

import { useEffect, useMemo, useState } from "react";
import { api } from "../lib/api.js";
import {
  ConfidenceChip, ErrorPanel, Loading, PageHeader, MetricCard,
} from "../components/Shared.jsx";
import MapplsMap from "../components/MapplsMap.jsx";

const CAUSES = [
  "vehicle_breakdown", "tree_fall", "construction", "accident",
  "water_logging", "vip_movement", "procession", "public_event",
];
const CORRIDORS = [
  "Mysore Road", "Tumkur Road", "Bellary Road 1", "Bellary Road 2",
  "Hosur Road", "ORR East 1", "ORR East 2", "ORR North 1", "ORR North 2",
  "Bannerghata Road", "Hennur Main Road", "Old Madras Road",
];

export default function AllocateView() {
  const [events, setEvents] = useState(seedEvents());
  const [nUnits, setNUnits] = useState(20);
  const [result, setResult] = useState(null);
  const [diversions, setDiversions] = useState({});
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);
  const [corridors, setCorridors] = useState([]);
  const [stations, setStations] = useState([]);

  const totalNeed = useMemo(
    () => events.reduce((s, e) => s + Math.max(2, Math.ceil((e.p90_min || 60) / 30)), 0),
    [events],
  );

  // Load the corridor + station layers once for the map
  useEffect(() => {
    let alive = true;
    Promise.all([api.riskCorridors(), api.mapStations()])
      .then(([rc, ms]) => {
        if (!alive) return;
        setCorridors(rc.corridors || []);
        setStations((ms.geojson?.features || []).map((f) => f.properties));
      })
      .catch(() => { /* map is best-effort */ });
    return () => { alive = false; };
  }, []);

  const run = async () => {
    setLoading(true); setError(null); setResult(null);
    try {
      const payload = {
        events: events.map((e, i) => ({
          id: e.id || `E${i + 1}`,
          corridor: e.corridor, cause: e.cause,
          p50_min: e.p50_min, p90_min: e.p90_min,
          closure_prob: e.closure_prob, corridor_risk: e.corridor_risk,
          is_planned: e.is_planned,
        })),
        units: Array.from({ length: nUnits }, (_, i) => ({
          id: `U${String(i + 1).padStart(3, "0")}`,
          station: `PS ${(i % 6) + 1}`,
          agency: ["police", "traffic", "BBMP", "BESCOM", "BWSSB"][i % 5],
        })),
      };
      const r = await api.optimize(payload);
      setResult(r);
      // Fetch live diversion routes for each event that has one
      // (spec 07 §"Build note" — real Mappls ETA in the optimizer output)
      const divs = {};
      await Promise.all(
        events.map(async (e) => {
          const target = e.corridor && r.events?.[e.id]?.diversion_route;
          if (target && target !== "nearest parallel arterial") {
            try {
              const d = await api.mapDiversion(e.corridor, target);
              divs[e.id] = d;
            } catch { /* skip on failure */ }
          }
        })
      );
      setDiversions(divs);
    } catch (e) { setError(e); }
    finally { setLoading(false); }
  };

  return (
    <div data-tour="allocate" className="flex h-full min-h-0 flex-col gap-2 overflow-y-auto p-3">
      <PageHeader
        title="Allocate resources across concurrent events"
        subtitle="PuLP ILP — jointly minimizes expected congestion-minutes under one shared budget, with cascade pre-positioning"
        actions={
          <div className="flex items-center gap-2">
            <label className="text-xs text-ink-300">Officers</label>
            <input
              type="number" min={5} max={200}
              className="input w-20"
              value={nUnits}
              onChange={(e) => setNUnits(parseInt(e.target.value, 10) || 5)}
            />
            <button className="btn-primary" onClick={run} disabled={loading}>
              {loading ? "Solving…" : "Run ILP"}
            </button>
          </div>
        }
      />
      <ErrorPanel error={error} />

      <section className="card p-4">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-ink-100">Concurrent events</h2>
          <div className="flex items-center gap-3 text-[11px] text-ink-400">
            <span>total need ≈ <span className="num text-ink-100">{totalNeed}</span></span>
            <span>vs pool <span className="num text-accent-400">{nUnits}</span></span>
            <button className="btn-ghost text-xs" onClick={() => setEvents(seedEvents())}>
              ↺ reset
            </button>
            <button className="btn-ghost text-xs" onClick={() => setEvents([...events, blankEvent(events.length + 1)])}>
              + add event
            </button>
          </div>
        </div>
        <EventTable events={events} setEvents={setEvents} />
      </section>

      {loading && <Loading label="PuLP ILP solving…" />}
      {result && <AllocationBoard result={result} corridors={corridors}
                              stations={stations} diversions={diversions} />}
    </div>
  );
}

function EventTable({ events, setEvents }) {
  const upd = (i, patch) => setEvents(events.map((e, j) => (i === j ? { ...e, ...patch } : e)));
  const del = (i) => setEvents(events.filter((_, j) => j !== i));
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="text-left text-[10px] uppercase tracking-wider text-ink-500">
          <tr>
            <th className="py-1 pr-2">id</th>
            <th className="py-1 pr-2">corridor</th>
            <th className="py-1 pr-2">cause</th>
            <th className="py-1 pr-2 text-right">P50</th>
            <th className="py-1 pr-2 text-right">P90</th>
            <th className="py-1 pr-2 text-right">closure</th>
            <th className="py-1 pr-2 text-right">risk</th>
            <th className="py-1 pr-2">planned</th>
            <th className="py-1 pr-2" />
          </tr>
        </thead>
        <tbody>
          {events.map((e, i) => (
            <tr key={i} className="border-t border-ink-800">
              <td className="py-1.5 pr-2 num text-ink-300">{e.id || `E${i + 1}`}</td>
              <td className="py-1.5 pr-2">
                <Select value={e.corridor} onChange={(v) => upd(i, { corridor: v })} options={CORRIDORS} />
              </td>
              <td className="py-1.5 pr-2">
                <Select value={e.cause} onChange={(v) => upd(i, { cause: v })} options={CAUSES} />
              </td>
              <td className="py-1.5 pr-2"><NumInput value={e.p50_min} onChange={(v) => upd(i, { p50_min: v })} /></td>
              <td className="py-1.5 pr-2"><NumInput value={e.p90_min} onChange={(v) => upd(i, { p90_min: v })} /></td>
              <td className="py-1.5 pr-2"><NumInput value={e.closure_prob} onChange={(v) => upd(i, { closure_prob: v })} step={0.05} /></td>
              <td className="py-1.5 pr-2"><NumInput value={e.corridor_risk} onChange={(v) => upd(i, { corridor_risk: v })} step={0.05} /></td>
              <td className="py-1.5 pr-2">
                <input type="checkbox" checked={!!e.is_planned} onChange={(ev) => upd(i, { is_planned: ev.target.checked })} />
              </td>
              <td className="py-1.5 pr-2">
                <button className="btn-ghost text-[11px]" onClick={() => del(i)}>×</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Select({ value, onChange, options }) {
  return (
    <select className="select" value={value} onChange={(e) => onChange(e.target.value)}>
      {options.map((o) => <option key={o} value={o}>{o}</option>)}
    </select>
  );
}

function NumInput({ value, onChange, step = 1 }) {
  return (
    <input
      type="number"
      step={step}
      className="input w-20 text-right num"
      value={value}
      onChange={(e) => onChange(parseFloat(e.target.value) || 0)}
    />
  );
}

function AllocationBoard({ result, corridors, stations, diversions }) {
  const events = Object.entries(result.events);
  const totalOfficers = events.reduce((s, [, a]) => s + a.officers, 0);
  const totalBarricades = events.reduce((s, [, a]) => s + a.barricades, 0);
  const understaffed = events.filter(([, a]) => a.understaffed_by > 0);

  // Build the diversion polylines for the map (one per event with a route)
  const divsForMap = Object.entries(diversions || {}).map(([eid, d]) => {
    const alloc = result.events[eid];
    return {
      id: eid,
      source: alloc?.corridor || d.source,
      target: d.target || alloc?.diversion_route,
      eta_min: d.eta_min,
      km: d.km,
      polyline: d.polyline || [],
      source_kind: d.polyline_source || d.source,
    };
  }).filter((d) => d.polyline && d.polyline.length > 1);

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <MetricCard label="ILP status" value={result.status} accent
                    sub={`solved in ${(result.solve_time_s * 1000).toFixed(0)}ms`} />
        <MetricCard label="Officers deployed" value={totalOfficers}
                    sub={`of ${result.summary.pool_cap} in pool`} />
        <MetricCard label="Barricades" value={totalBarricades}
                    sub="allocated to closure tiers" />
        <MetricCard label="Understaffed events"
                    value={understaffed.length}
                    sub={understaffed.length ? "tight budget" : "fully covered"} />
      </div>

      {/* spec 07 — diversion polylines on the Bengaluru map */}
      {divsForMap.length > 0 && (
        <section className="card p-4">
          <div className="mb-2 flex items-center justify-between">
            <h2 className="text-sm font-semibold text-ink-100">Diversion routes</h2>
            <span className="text-[10px] text-ink-500">
              live Mappls DM · polyline synthesized (no routing key)
            </span>
          </div>
          <MapplsMap
            corridors={corridors || []}
            stations={stations || []}
            diversions={divsForMap}
            highlight={null}
            width={720}
            height={380}
          />
        </section>
      )}

      {result.summary.pre_positioned_corridors && result.summary.pre_positioned_corridors.length > 0 && (
        <div className="card border-accent-500/30 bg-accent-500/5 p-4">
          <h3 className="mb-2 text-sm font-semibold text-ink-100">
            ⚠ Cascade pre-positioned corridors
          </h3>
          <div className="space-y-1 text-sm">
            {result.summary.pre_positioned_corridors.map((c, i) => (
              <div key={i} className="flex items-center gap-2 text-ink-200">
                <span>{c.source}</span>
                <span className="text-ink-500">→</span>
                <span className="text-ink-100">{c.target}</span>
                <span className="num text-accent-400">{c.lag_min}m lead</span>
              </div>
            ))}
          </div>
        </div>
      )}

      <section className="card p-4">
        <h2 className="mb-3 text-sm font-semibold text-ink-100">Allocation board</h2>
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
          {events.map(([eid, a]) => (
            <AllocCard key={eid} id={eid} a={a} />
          ))}
        </div>
      </section>
    </div>
  );
}

function AllocCard({ id, a }) {
  return (
    <div className="card-tight p-3">
      <div className="mb-1 flex items-center justify-between">
        <span className="font-mono text-xs text-ink-300">{id}</span>
        {a.understaffed_by > 0
          ? <span className="pill-warn">−{a.understaffed_by}</span>
          : <span className="pill-good">covered</span>}
      </div>
      <div className="grid grid-cols-3 gap-2 text-sm">
        <Stat label="officers" value={a.officers} accent />
        <Stat label="barricades" value={a.barricades} />
        <Stat label="lead" value={`${a.pre_deploy_lead_time}m`} />
      </div>
      {a.diversion_route && (
        <div className="mt-2 text-[11px] text-ink-300">
          ↪ divert to <span className="text-ink-100">{a.diversion_route}</span>
        </div>
      )}
      <details className="mt-2">
        <summary className="cursor-pointer text-[10px] uppercase tracking-wider text-ink-400 hover:text-ink-200">
          because ({a.because.length})
        </summary>
        <ul className="mt-1 space-y-0.5 text-[11px] text-ink-300">
          {a.because.map((b, i) => <li key={i}>· {b}</li>)}
        </ul>
      </details>
    </div>
  );
}

function Stat({ label, value, accent }) {
  return (
    <div>
      <div className="label">{label}</div>
      <div className={"num text-base " + (accent ? "text-accent-400" : "text-ink-100")}>
        {value}
      </div>
    </div>
  );
}

function seedEvents() {
  return [
    blankEvent(1, { corridor: "Mysore Road",   cause: "tree_fall",
                    p50_min: 60, p90_min: 240, closure_prob: 0.55,
                    corridor_risk: 0.42 }),
    blankEvent(2, { corridor: "Tumkur Road",   cause: "vip_movement",
                    p50_min: 40, p90_min: 90,  closure_prob: 0.35,
                    corridor_risk: 0.13, is_planned: true }),
    blankEvent(3, { corridor: "ORR East 1",    cause: "accident",
                    p50_min: 50, p90_min: 120, closure_prob: 0.20,
                    corridor_risk: 0.27 }),
  ];
}

function blankEvent(n, patch = {}) {
  return {
    id: `E${n}`,
    corridor: "Non-corridor", cause: "vehicle_breakdown",
    p50_min: 45, p90_min: 120, closure_prob: 0.10, corridor_risk: 0.30,
    is_planned: false, ...patch,
  };
}
