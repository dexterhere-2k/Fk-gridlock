// View 1 — Live map + corridor heatmap + cascade overlay
// Per spec 05 §"Required views": corridor risk prior as backdrop,
// cascade arrows overlay, replay-driven WebSocket pulses on top.
// Per spec 07: corridor heatmap + police stations are rendered as a
// pure-SVG schematic of Bengaluru (the provided Mappls REST key does
// not authorize the Map SDK base tiles; we disclose this honestly in
// the limitations badge below the map).

import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../lib/api.js";
import { useLiveStatus } from "../hooks/useLiveStatus.js";
import {
  ErrorPanel, Loading, PageHeader, MetricCard,
} from "../components/Shared.jsx";
import MapplsMap from "../components/MapplsMap.jsx";

// Hard-coded Bengaluru center + corridor coords (mirrors
// src/mappls_service.DEFAULT_CORRIDOR_COORDS). We bundle them here so
// the front-end map works even if the /api/map/* endpoints are slow
// to respond on first load.
const BENGALURU_CENTER = [12.9716, 77.5946];
const CORRIDOR_COORDS = {
  "Mysore Road":          [12.9858, 77.5210],
  "Tumkur Road":          [13.0293, 77.5365],
  "Bellary Road 1":       [13.0588, 77.5861],
  "Bellary Road 2":       [13.0728, 77.5936],
  "Hosur Road":           [12.9416, 77.6252],
  "ORR East 1":           [12.9940, 77.7160],
  "ORR East 2":           [12.9805, 77.7600],
  "ORR North 1":          [13.0610, 77.6510],
  "ORR North 2":          [13.0820, 77.6890],
  "ORR West 1":           [12.9510, 77.5180],
  "Bannerghata Road":     [12.9050, 77.6020],
  "Hennur Main Road":     [13.0610, 77.6470],
  "Old Madras Road":      [13.0040, 77.6650],
  "Old Airport Road":     [12.9580, 77.6510],
  "Varthur Road":         [12.9500, 77.7170],
  "Magadi Road":          [12.9760, 77.5180],
  "West of Chord Road":   [12.9990, 77.5320],
  "CBD 1":                [12.9760, 77.6020],
  "CBD 2":                [12.9650, 77.6020],
  "Airport New South Road": [13.1990, 77.7060],
  "IRR(Thanisandra road)":[13.0580, 77.6280],
  "Non-corridor":         BENGALURU_CENTER,
};
const STATION_COORDS = {
  "Yeshwanthpura PS": [13.0270, 77.5390],
  "Hebbal PS":         [13.0500, 77.5910],
  "Rajajinagar PS":    [12.9910, 77.5510],
  "HSR PS":            [12.9116, 77.6473],
  "Whitefield PS":     [12.9698, 77.7500],
  "Electronic City PS":[12.8452, 77.6603],
  "Indiranagar PS":    [12.9716, 77.6412],
  "Koramangala PS":    [12.9352, 77.6245],
  "MG Road PS":        [12.9756, 77.6063],
  "Jayanagar PS":      [12.9279, 77.5938],
};

function _withCoords(c) {
  const ll = CORRIDOR_COORDS[c.corridor] || BENGALURU_CENTER;
  return { ...c, lat: ll[0], lon: ll[1] };
}

function _stationWithCoords(s) {
  const ll = STATION_COORDS[s.name] || BENGALURU_CENTER;
  return { ...s, lat: ll[0], lon: ll[1] };
}

export default function LiveView() {
  const [corridors, setCorridors] = useState(null);
  const [cascade, setCascade] = useState(null);
  const [incidents, setIncidents] = useState([]);
  const [stations, setStations] = useState([]);
  const [coverage, setCoverage] = useState(null);
  const [error, setError] = useState(null);
  const [selectedCorridor, setSelectedCorridor] = useState(null);
  const [pulseCorridor, setPulseCorridor] = useState(null);
  const { status: wsStatus, messages } = useLiveStatus();

  // Pulse-ring the most-recent WebSocket corridor for ~3s, then drop it.
  // Throttled: only `corridor_pulse` (not resolved/cascade_alert), only
  // events with a real ETA (>=5m), only non-Non-corridor, and at most
  // once per 1.5s per corridor (avoid stacking back-to-back pulses).
  const pulseRef = useRef({ lastByCorridor: {}, lastTs: 0 });
  useEffect(() => {
    if (!messages || messages.length === 0) return;
    const last = messages[messages.length - 1];
    if (!last || !last.corridor) return;
    if (last.kind && last.kind !== "corridor_pulse") return;     // only incidents
    if (last.corridor === "Non-corridor") return;                // not a real corridor
    if (!last.eta_min || last.eta_min < 5) return;                // ignore zero-eta ghosts
    const now = Date.now();
    if (now - pulseRef.current.lastTs < 800) return;             // global cooldown
    if (now - (pulseRef.current.lastByCorridor[last.corridor] || 0) < 1500) return; // per-corridor
    pulseRef.current.lastTs = now;
    pulseRef.current.lastByCorridor[last.corridor] = now;
    setPulseCorridor(last.corridor);
    const t = setTimeout(() => setPulseCorridor(null), 3000);
    return () => clearTimeout(t);
  }, [messages]);

  useEffect(() => {
    let alive = true;
    Promise.all([
      api.riskCorridors(), api.cascadeGraph(), api.mapIncidents(200),
      api.mapStations(), api.mapHealth(),
    ])
      .then(([rc, cg, mi, ms, mh]) => {
        if (!alive) return;
        setCorridors(rc.corridors);
        setCascade(cg);
        setIncidents((mi.geojson?.features || []).map((f) => f.properties));
        setStations((ms.geojson?.features || []).map((f) => f.properties));
        setCoverage(mh);
      })
      .catch((e) => alive && setError(e));
    return () => { alive = false; };
  }, []);

  const top = useMemo(
    () => (corridors ? [...corridors].sort((a, b) => b.risk_score - a.risk_score).slice(0, 5) : []),
    [corridors],
  );
  const latest = messages.slice(-3).reverse();
  const corridorsWithCoords = useMemo(
    () => (corridors || []).map(_withCoords), [corridors]);
  const stationsWithCoords = useMemo(
    () => stations.map(_stationWithCoords), [stations]);
  // Top-3 cascade edges rendered as polylines on the map
  const cascadeDiversions = useMemo(() => {
    if (!cascade || !corridors) return [];
    return (cascade.strongest_edges || []).slice(0, 3).map((e) => ({
      id: `${e.source}→${e.target}`,
      source: e.source, target: e.target,
      eta_min: Math.round(e.lag_h * 60),
      km: 0,    // the cascade edge is a temporal, not spatial, link
      polyline: [
        { lat: BENGALURU_CENTER[0], lon: BENGALURU_CENTER[1] },
      ],
      source_kind: "cascade",
    }));
  }, [cascade, corridors]);

  return (
    <div className="space-y-4">
      <PageHeader
        title="Live corridor status"
        subtitle="Risk-prior backdrop + cascade overlay + replay-driven live pulse"
        actions={
          <div className="flex items-center gap-2">
            {coverage && (
              <span className={coverage.has_credentials ? "pill-good" : "pill-warn"}
                    title="Mappls product coverage (spec 07)">
                Mappls: {Object.values(coverage.coverage || {}).filter(c => c.status === "live").length}/5 live
              </span>
            )}
            {/* spec 08 #7 — Link to ops dashboard for cascade pre-alerts */}
            <a href="/ops"
               className="pill-ink hover:bg-ink-700 transition"
               title="Open Ops Command Center (spec 08)">
              🎛 Ops
            </a>
            <span className="pill-ink">
              <span className="h-1.5 w-1.5 rounded-full bg-accent-500 animate-pulse" />
              WS: {wsStatus}
            </span>
          </div>
        }
      />
      <ErrorPanel error={error} />

      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        <MetricCard label="Corridors tracked" value={corridors?.length ?? "–"} sub="risk prior (01)" />
        <MetricCard
          label="Cascade edges"
          value={cascade?.n_edges ?? "–"}
          accent
          sub={`${cascade?.n_corridors ?? "–"} corridors, ${cascade?.n_hours ?? "–"} hours`}
        />
        <MetricCard
          label="Top trigger"
          value={cascade?.trigger_rank?.[0]?.corridor || "–"}
          sub={cascade?.trigger_rank?.[0]
            ? `${cascade.trigger_rank[0].downstream_count} downstream · r=${cascade.trigger_rank[0].max_r?.toFixed(2)}`
            : "—"}
          onClick={() => setSelectedCorridor(cascade?.trigger_rank?.[0]?.corridor || null)}
          active={selectedCorridor === cascade?.trigger_rank?.[0]?.corridor}
        />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        {/* Mappls schematic map (spec 07) */}
        <section data-tour="map" className="card p-4 lg:col-span-2">
          <div className="mb-2 flex items-center justify-between">
            <div>
              <h2 className="text-sm font-semibold text-ink-100">Bengaluru command map</h2>
              <div className="text-[10px] text-ink-500">
                spec 07 · Mappls DM live · base map © OpenStreetMap
              </div>
            </div>
            <div className="flex items-center gap-2">
              {selectedCorridor && (
                <button
                  onClick={() => setSelectedCorridor(null)}
                  className="rounded border border-ink-700 bg-ink-800 px-2 py-0.5 text-[10px] text-ink-300 hover:bg-ink-700"
                >
                  ✕ clear focus
                </button>
              )}
              <span className="label">22 corridors · 10 stations</span>
            </div>
          </div>
          {corridors ? (
            <MapplsMap
              corridors={corridorsWithCoords}
              incidents={incidents}
              stations={stationsWithCoords}
              diversions={cascadeDiversions}
              highlight={selectedCorridor}
              pulseCorridor={pulseCorridor}
              onCorridorClick={(c) => setSelectedCorridor(c.corridor)}
            />
          ) : <Loading label="loading map…" />}
          {coverage && coverage.limitations && coverage.limitations.length > 0 && (
            <details className="mt-2 text-[10px] text-ink-500">
              <summary className="cursor-pointer text-ink-400">
                ⚠ Mappls coverage: {coverage.limitations.length} limitations
              </summary>
              <ul className="mt-1 list-disc pl-4 space-y-0.5">
                {coverage.limitations.map((l, i) => <li key={i}>{l}</li>)}
              </ul>
            </details>
          )}
        </section>

        {/* cascade + live pulse */}
        <section className="card p-4">
          <h2 className="mb-2 text-sm font-semibold text-ink-100">Cascade graph</h2>
          {cascade && Array.isArray(cascade.strongest_edges) && cascade.strongest_edges.length > 0 ? (
            <div className="space-y-2 text-sm">
              {cascade.strongest_edges.slice(0, 6).map((e, i) => (
                <div key={i} className="space-y-0.5">
                  <div className="flex items-center gap-1 truncate">
                    <span className="truncate text-ink-200">{e.source}</span>
                    <span className="text-ink-500">→</span>
                    <span className="truncate text-ink-200">{e.target}</span>
                  </div>
                  <div className="ml-1 text-[10px] text-ink-400">
                    <span className="num">lag {e.lag_h}h</span>
                    <span className="mx-1">·</span>
                    <span className="num text-accent-400">r={e.r.toFixed(2)}</span>
                  </div>
                </div>
              ))}
            </div>
          ) : <Loading label="loading cascade…" />}
          <div className="mt-4 border-t border-ink-800 pt-3">
            <h3 className="mb-1 text-[11px] uppercase tracking-wider text-ink-400">Live pulse (last 3)</h3>
            {latest.length === 0 ? (
              <p className="text-xs text-ink-500">no pulses yet…</p>
            ) : latest.map((m, i) => (
              <div key={i} className="mb-1.5 flex flex-col gap-0.5 text-xs">
                <div className="flex items-center gap-1.5">
                  <span className="h-1.5 w-1.5 rounded-full bg-accent-500 animate-pulse" />
                  <span className="truncate text-ink-200">{m.corridor}</span>
                </div>
                <div className="ml-3 flex items-center gap-2 text-[10px] text-ink-400">
                  <span className="pill-ink uppercase">{m.kind?.replace("_", " ")}</span>
                  <span className="num text-accent-400">r={m.risk_score?.toFixed(2)}</span>
                  <span className="num">eta {m.eta_min}m</span>
                  {m.closure && <span className="pill-warn">closure</span>}
                </div>
              </div>
            ))}
          </div>
        </section>
      </div>

      {/* top risk table */}
      <section className="card p-4">
        <h2 className="mb-3 text-sm font-semibold text-ink-100">Top 5 high-risk corridors</h2>
        {top.length === 0 ? <Loading label="…" /> : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-[10px] uppercase tracking-wider text-ink-500">
                <tr>
                  <th className="py-1 pr-3">corridor</th>
                  <th className="py-1 pr-3 text-right">events</th>
                  <th className="py-1 pr-3 text-right">med clear</th>
                  <th className="py-1 pr-3 text-right">p90 clear</th>
                  <th className="py-1 pr-3 text-right">closure rate</th>
                  <th className="py-1 pr-3 text-right">risk</th>
                </tr>
              </thead>
              <tbody>
                {top.map((c) => (
                  <tr key={c.corridor} className="border-t border-ink-800">
                    <td className="py-1.5 pr-3 font-medium text-ink-100">{c.corridor}</td>
                    <td className="py-1.5 pr-3 text-right num">{c.events}</td>
                    <td className="py-1.5 pr-3 text-right num text-ink-300">{Math.round(c.med_clear)}m</td>
                    <td className="py-1.5 pr-3 text-right num text-ink-300">{Math.round(c.p90_clear)}m</td>
                    <td className="py-1.5 pr-3 text-right num text-ink-300">{(c.closure_rate * 100).toFixed(1)}%</td>
                    <td className="py-1.5 pr-3 text-right">
                      <RiskBar score={c.risk_score} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

function Heatmap({ corridors, latest }) {
  // The "map" — a tile of corridors sized by event count and colored
  // by risk score. Pinned, the live pulse highlights the most recent
  // corridor. Replace with Mappls in spec 07.
  const maxEvents = Math.max(...corridors.map((c) => c.events), 1);
  const latestCorridor = latest[0]?.corridor;
  return (
    <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-3 md:grid-cols-4">
      {corridors.map((c) => {
        const size = 0.6 + 0.4 * (c.events / maxEvents);
        const heat = Math.min(1, c.risk_score);
        const isLive = c.corridor === latestCorridor;
        return (
          <div
            key={c.corridor}
            className={
              "relative aspect-[2/1] overflow-hidden rounded-md border " +
              (isLive
                ? "border-accent-500/80 shadow-glow"
                : "border-ink-800/80")
            }
            style={{
              background: `linear-gradient(135deg, rgba(31,122,248,${0.05 + heat * 0.45}), rgba(220,38,38,${0.05 + heat * 0.5}))`,
            }}
            title={`${c.corridor} · risk=${c.risk_score.toFixed(2)} · events=${c.events}`}
          >
            <div className="absolute inset-0 flex flex-col justify-end p-1.5"
                 style={{ fontSize: `${10 + size * 4}px` }}>
              <div className="truncate font-semibold text-ink-50">
                {c.corridor}
              </div>
              <div className="flex items-center justify-between text-[10px] text-ink-200/80">
                <span className="num">{c.events} evt</span>
                <span className="num">r={c.risk_score.toFixed(2)}</span>
              </div>
            </div>
            {isLive ? (
              <div className="absolute right-1 top-1 h-1.5 w-1.5 rounded-full bg-accent-400 animate-pulse" />
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

function RiskBar({ score }) {
  const pct = Math.round(Math.min(1, score) * 100);
  const color = score > 0.6 ? "bg-bad-500" : score > 0.3 ? "bg-warn-500" : "bg-good-500";
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-20 overflow-hidden rounded-full bg-ink-800">
        <div className={"h-1.5 " + color} style={{ width: `${pct}%` }} />
      </div>
      <span className="num text-xs">{score.toFixed(2)}</span>
    </div>
  );
}
