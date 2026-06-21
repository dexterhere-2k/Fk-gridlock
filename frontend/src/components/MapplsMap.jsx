// GridLock — 07: Bengaluru command map (real OSM-derived basemap
// + interactive SVG overlay).
//
// Per spec 07 §"Demo-safety": the provided Mappls REST key only
// authorizes the Distance Matrix product, NOT the Map SDK base
// tiles. We use a static OSM-derived raster of Bengaluru as the
// map background (pre-fetched at build time, no per-request tile
// fetch, no rate-limit problem). All overlays (corridor risk dots,
// police stations, incident pins, diversion polylines) are rendered
// in SVG, so they stay sharp at any zoom and we can attach hover
// tooltips to every feature.
//
// Layers (bottom → top):
//   0. basemap.png   — OpenStreetMap raster (real Bengaluru streets)
//   1. corridor_heatmap — 22 corridors as colored circles
//   2. incidents    — historical incident pins (small dots)
//   3. diversions   — polylines (one per allocated event)
//   4. stations     — police stations (blue dots with white halo)
//   5. tooltip      — pure-SVG hover tooltip
//   6. pulse-ring   — animated halo on the most recent live corridor
//
// All overlay text uses a dark ink palette for contrast on the
// light OSM map. The page chrome around the map stays dark in
// dark mode, but the map itself is always light (cartographic
// convention — easier on the eyes for sustained viewing).
//
// Interactivity (spec 05 "click to drill in" + spec 08 #7):
//   - click a corridor dot → onCorridorClick (parent decides what to do)
//   - hover any feature → tooltip with metrics
//   - zoom in / zoom out / reset buttons (smooth viewBox animation)
//   - pulse-ring animation for the live corridor (`pulseCorridor` prop)
//
// Hover tooltips on every feature.

import { useEffect, useMemo, useRef, useState } from "react";

// Bengaluru bounding box. MUST match the bbox used to build
// public/blr_basemap.png (see scripts/build_basemap.py).
const BBOX = { minLat: 12.84, maxLat: 13.22, minLon: 77.44, maxLon: 77.80 };
// SVG canvas dimensions = basemap pixel size (548×592) so overlays
// align 1:1 with the raster.
const W = 548, H = 592;
const ASPECT = W / H;  // 0.9257 (taller than wide)

// Zoom levels (viewBox scale factors)
const ZOOM_LEVELS = [
  { scale: 1.0,  label: "Fit" },
  { scale: 1.8,  label: "2x"  },
  { scale: 3.2,  label: "4x"  },
];

// Helper: cubic ease-out for viewBox animations
const easeOut = (t) => 1 - Math.pow(1 - t, 3);

function project(lat, lon) {
  if (typeof lat !== "number" || typeof lon !== "number" ||
      !Number.isFinite(lat) || !Number.isFinite(lon)) {
    return { x: NaN, y: NaN };
  }
  const x = ((lon - BBOX.minLon) / (BBOX.maxLon - BBOX.minLon)) * W;
  const y = (1 - (lat - BBOX.minLat) / (BBOX.maxLat - BBOX.minLat)) * H;
  return { x, y };
}

// Drop features with missing/non-finite coords (NaN cx/cy crashes
// SVG rendering and pollutes the dev console).
const FINITE = (p) => Number.isFinite(p._x) && Number.isFinite(p._y);

const CORRIDOR_COLOR = (s) =>
  s >= 0.6 ? "#dc2626" : s >= 0.3 ? "#f59e0b" : "#16a34a";

const CATEGORY_ICON = {
  tree_fall:        "🌳",
  vip:              "🚁",
  protest:          "✊",
  water:            "💧",
  construction:     "🚧",
  procession:       "🚶",
  public_event:     "🎪",
  accident:         "💥",
  breakdown:        "🔧",
  pothole:          "🕳️",
  congestion:       "🚦",
  other:            "📍",
};

function Tooltip({ x, y, kind, data }) {
  if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
  // Render the tooltip as plain SVG (rect + text) inside a <g>, so we
  // never have to embed HTML inside <foreignObject>. This sidesteps
  // the React 18 dev-mode "incorrect casing" warning entirely.
  const lines =
    kind === "corridor" ? [
      data.corridor,
      `events: ${data.events}`,
      `closure: ${(data.closure_rate * 100).toFixed(1)}%`,
      `risk: ${data.risk_score?.toFixed(2)}`,
    ] :
    kind === "station" ? [
      data.name,
      "police station",
      `${data.distance_km?.toFixed(1)} km from center`,
    ] :
    kind === "incident" ? [
      `${CATEGORY_ICON[data.icon] || "📍"} ${data.cause}`,
      `corridor: ${data.corridor}`,
      `closure: ${data.closure ? "yes" : "no"}`,
      `planned: ${data.is_planned ? "yes" : "no"}`,
    ] :
    kind === "diversion" ? [
      `${data.source} → ${data.target}`,
      `eta: ${data.eta_min} min  ·  ${data.km} km`,
      `source: ${data.source_kind}`,
    ] : [];

  if (lines.length === 0) return null;
  const TIP_W = 220, TIP_H = 16 + lines.length * 14;
  const tx = Math.min(W - TIP_W - 8, x + 8);
  const ty = Math.max(4, y - TIP_H - 8);
  return (
    <g style={{ pointerEvents: "none" }}>
      <rect x={tx} y={ty} width={TIP_W} height={TIP_H} rx={5}
            fill="rgba(15, 18, 24, 0.96)"
            stroke="rgba(255,255,255,0.18)" strokeWidth="1" />
      {lines.map((line, i) => (
        <text key={i}
              x={tx + 8}
              y={ty + 12 + i * 14}
              fontSize={i === 0 ? 11 : 10}
              fontWeight={i === 0 ? 600 : 400}
              fill={i === 0 ? "#f6f7f9" : "#cbd5e1"}>
          {line}
        </text>
      ))}
    </g>
  );
}

export default function MapplsMap({
  corridors = [],         // [{corridor, risk_score, closure_rate, n_events, ...}]
  incidents = [],         // [{id, cause, lat, lon, icon, ...}]
  stations = [],          // [{name, lat, lon, distance_km}]
  diversions = [],        // [{id, source, target, eta_min, km, polyline, source_kind}]
  highlight = null,       // optional id to highlight (zoom + outline)
  pulseCorridor = null,   // corridor name to pulse-ring (WebSocket-driven)
  onCorridorClick = null,  // (corridor) => void
  onStationClick = null,   // (station) => void
  onIncidentClick = null,  // (incident) => void
  showLabels = true,
  className = "",
}) {
  const [hover, setHover] = useState(null);
  // ---- viewBox zoom/pan (Phase 2.2)
  const [zoomIdx, setZoomIdx] = useState(0);
  const [vb, setVb] = useState({ x: 0, y: 0, w: W, h: H });
  // smooth animation frame for viewBox transitions
  const rafRef = useRef(null);

  // ---- Pre-project every feature to SVG coords once.
  const projected = useMemo(() => {
    const cs = corridors.map((c) => {
      const p = project(c.lat, c.lon);
      return { ...c, _x: p.x, _y: p.y };
    });
    const is = incidents.map((i) => {
      const p = project(i.lat, i.lon);
      return { ...i, _x: p.x, _y: p.y };
    });
    const ss = stations.map((s) => {
      const p = project(s.lat, s.lon);
      return { ...s, _x: p.x, _y: p.y };
    });
    const ds = diversions.map((d) => {
      const poly = (d.polyline || []).map((pt) => project(pt.lat, pt.lon));
      return { ...d, _poly: poly };
    });
    return { cs, is, ss, ds };
  }, [corridors, incidents, stations, diversions]);

  // ---- Apply zoom: center on the highlighted corridor, or fit-all
  useEffect(() => {
    if (rafRef.current) cancelAnimationFrame(rafRef.current);
    let target;
    const z = ZOOM_LEVELS[zoomIdx];
    if (highlight) {
      // zoom to highlight: find its projected position
      const f = projected.cs.find((c) => c.corridor === highlight)
             || projected.is.find((i) => i.id === highlight)
             || projected.ss.find((s) => s.name === highlight);
      if (f && Number.isFinite(f._x)) {
        const w = W / z.scale, h = H / z.scale;
        target = { x: f._x - w / 2, y: f._y - h / 2, w, h };
      }
    }
    if (!target) {
      target = { x: 0, y: 0, w: W / z.scale, h: H / z.scale };
    }
    // clamp so we don't pan off the image
    target.x = Math.max(0, Math.min(W - target.w, target.x));
    target.y = Math.max(0, Math.min(H - target.h, target.y));

    // animate from current vb to target
    const start = vb;
    const t0 = performance.now();
    const dur = 350;
    const step = (now) => {
      const t = Math.min(1, (now - t0) / dur);
      const k = easeOut(t);
      setVb({
        x: start.x + (target.x - start.x) * k,
        y: start.y + (target.y - start.y) * k,
        w: start.w + (target.w - start.w) * k,
        h: start.h + (target.h - start.h) * k,
      });
      if (t < 1) rafRef.current = requestAnimationFrame(step);
    };
    rafRef.current = requestAnimationFrame(step);
    return () => rafRef.current && cancelAnimationFrame(rafRef.current);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [zoomIdx, highlight, projected]);

  return (
    <div
      className={
        "relative w-full overflow-hidden rounded-xl border border-ink-800 bg-white shadow-card " +
        className
      }
      style={{ aspectRatio: ASPECT }}
    >
      <svg
        viewBox={`${vb.x} ${vb.y} ${vb.w} ${vb.h}`}
        width="100%" height="100%"
        preserveAspectRatio="xMidYMid meet"
        role="img"
        aria-label="GridLock Bengaluru command map"
        style={{ display: "block" }}
      >
        {/* ---- 0. basemap (real OSM-derived raster of Bengaluru) */}
        <image href="/blr_basemap.png" x="0" y="0" width={W} height={H}
               preserveAspectRatio="none"
               style={{
                 filter: "saturate(0.92) brightness(1.02) drop-shadow(0 1px 2px rgba(0,0,0,0.1))",
               }} />

        {/* ---- 1. corridor heatmap dots (overlaid on real streets) */}
        {projected.cs.filter(FINITE).map((c) => {
          const isHighlight = highlight === c.corridor;
          const isPulse = pulseCorridor === c.corridor;
          return (
            <g key={c.corridor}
               onMouseEnter={() => setHover({ kind: "corridor", c })}
               onMouseLeave={() => setHover(null)}
               onClick={() => onCorridorClick && onCorridorClick(c)}
               style={{ cursor: onCorridorClick ? "pointer" : "default" }}>
              {/* white halo for legibility on the basemap */}
              <circle cx={c._x} cy={c._y}
                      r={Math.max(10, Math.min(24, 8 + (c.events || 0) / 50))}
                      fill="#ffffff" fillOpacity="0.85"
                      style={{ pointerEvents: "none" }} />
              <circle cx={c._x} cy={c._y}
                      r={Math.max(8, Math.min(22, 6 + (c.events || 0) / 50))}
                      fill={CORRIDOR_COLOR(c.risk_score || 0)}
                      fillOpacity={isHighlight ? 1.0 : 0.78}
                      stroke={isHighlight ? "#0f1218" : "#1a1f28"}
                      strokeWidth={isHighlight ? 2.5 : 1.5}
                      style={{
                        // Scope the transition to ONLY the highlighted
                        // corridor. Animating all 22 dots on every
                        // re-render causes a "shimmer" of radius/fill
                        // changes across the entire map. The non-
                        // highlighted ones snap instantly.
                        transition: isHighlight
                          ? "r 220ms ease-out, fill-opacity 220ms ease-out, stroke-width 220ms ease-out"
                          : "none",
                      }} />
              {/* pulse-ring for the most-recent live corridor (Phase 2.3) */}
              {isPulse && (
                <>
                  <circle cx={c._x} cy={c._y} r={12}
                          fill="none" stroke="#a855f7" strokeWidth="2.5"
                          style={{ animation: "gl-pulse-ring 1.6s ease-out infinite" }} />
                  <circle cx={c._x} cy={c._y} r={12}
                          fill="none" stroke="#a855f7" strokeWidth="2.5"
                          style={{ animation: "gl-pulse-ring 1.6s ease-out infinite 0.8s" }} />
                </>
              )}
              {showLabels && (
                <text x={c._x} y={c._y + 3} textAnchor="middle"
                      fontSize="8" fill="#0f1218"
                      style={{ pointerEvents: "none",
                              fontWeight: 700,
                              paintOrder: "stroke",
                              stroke: "rgba(255,255,255,0.9)",
                              strokeWidth: 3 }}>
                  {c.corridor.length > 18 ? c.corridor.slice(0, 16) + "…" : c.corridor}
                </text>
              )}
            </g>
          );
        })}

        {/* ---- 2. diversion polylines (drawn ABOVE corridors, BELOW pins) */}
        {projected.ds.map((d, i) => {
          if (!d._poly || d._poly.length < 2) return null;
          if (d._poly.some((p) => !Number.isFinite(p.x) || !Number.isFinite(p.y))) return null;
          const path = d._poly.map((p, j) => (j === 0 ? "M" : "L") + p.x + " " + p.y).join(" ");
          const isHi = highlight === d.id;
          return (
            <g key={"div" + i}>
              <path d={path} fill="none"
                    stroke={isHi ? "#0c4ab0" : "#1f7af8"}
                    strokeWidth={isHi ? 4 : 2.5}
                    strokeOpacity={isHi ? 0.95 : 0.85}
                    strokeDasharray={d.source_kind === "fallback_haversine_polyline" ? "6 4" : "none"}
                    onMouseEnter={() => setHover({ kind: "diversion", d })}
                    onMouseLeave={() => setHover(null)}
                    style={{ cursor: "pointer", transition: "stroke 220ms ease-out, stroke-width 220ms ease-out" }} />
              <circle cx={d._poly[0].x} cy={d._poly[0].y} r="4" fill="#16a34a" stroke="#fff" strokeWidth="1.5" />
              <circle cx={d._poly[d._poly.length - 1].x} cy={d._poly[d._poly.length - 1].y}
                      r="4" fill="#dc2626" stroke="#fff" strokeWidth="1.5" />
            </g>
          );
        })}

        {/* ---- 3. police stations (blue dots, always on top) */}
        {projected.ss.filter(FINITE).map((s, i) => (
          <g key={"st" + i}
             onMouseEnter={() => setHover({ kind: "station", s })}
             onMouseLeave={() => setHover(null)}
             onClick={() => onStationClick && onStationClick(s)}
             style={{ cursor: onStationClick ? "pointer" : "default" }}>
            <circle cx={s._x} cy={s._y} r="7" fill="#ffffff" fillOpacity="0.9"
                    style={{ pointerEvents: "none" }} />
            <circle cx={s._x} cy={s._y} r="4.5" fill="#1d4ed8" stroke="#fff" strokeWidth="1.5" />
            {showLabels && (
              <text x={s._x + 7} y={s._y + 3} fontSize="7" fill="#0f1218"
                    style={{ fontWeight: 600,
                             paintOrder: "stroke",
                             stroke: "rgba(255,255,255,0.9)",
                             strokeWidth: 2.5 }}>
                {s.name}
              </text>
            )}
          </g>
        ))}

        {/* ---- 4. incident pins (top layer) */}
        {projected.is.filter(FINITE).slice(0, 200).map((i, k) => (
          <g key={"in" + k}
             onMouseEnter={() => setHover({ kind: "incident", i })}
             onMouseLeave={() => setHover(null)}
             onClick={() => onIncidentClick && onIncidentClick(i)}
             style={{ cursor: onIncidentClick ? "pointer" : "default" }}>
            <circle cx={i._x} cy={i._y} r="3.5"
                    fill={i.closure ? "#dc2626" : i.is_planned ? "#f59e0b" : "#6b7280"}
                    fillOpacity="0.8"
                    stroke="#fff" strokeWidth="0.5"
                    style={{ transition: "fill 220ms ease-out" }} />
            {highlight === i.id && (
              <circle cx={i._x} cy={i._y} r="8" fill="none"
                      stroke="#0c4ab0" strokeWidth="2" />
            )}
          </g>
        ))}

        {/* ---- 5. tooltip */}
        {hover && (
          <Tooltip
            x={hover.c?._x ?? hover.d?._poly?.[0]?.x ?? hover.s?._x ?? hover.i?._x ?? 0}
            y={hover.c?._y ?? hover.s?._y ?? hover.i?._y ?? 0}
            kind={hover.kind}
            data={hover.c ?? hover.s ?? hover.i ?? hover.d}
          />
        )}
      </svg>

      {/* ---- 6. zoom controls (HTML overlay, outside the SVG) */}
      <div className="absolute right-2 top-2 flex flex-col rounded-md border border-ink-700 bg-ink-900/90 shadow-card">
        {ZOOM_LEVELS.map((z, i) => (
          <button
            key={z.label}
            onClick={() => setZoomIdx(i)}
            title={`${z.label} zoom`}
            className={
              "px-2 py-1 text-[10px] font-mono transition " +
              (i === zoomIdx
                ? "bg-accent-600 text-white"
                : "text-ink-300 hover:bg-ink-800 hover:text-ink-100")
            }
          >{z.label}</button>
        ))}
      </div>

      {/* ---- 7. base-map credit (HTML overlay, outside the SVG) */}
      <div className="absolute right-2 bottom-2 rounded bg-ink-900/80 px-2 py-0.5 text-[9px] text-ink-300">
        base map © OpenStreetMap
      </div>

      {/* ---- 8. legend (HTML overlay) */}
      <div className="absolute left-2 bottom-2 rounded-md border border-ink-700 bg-ink-900/95 px-2.5 py-1.5 shadow-card">
        <div className="text-[8px] uppercase tracking-wider text-ink-500">corridor risk</div>
        <div className="mt-0.5 flex items-center gap-2.5 text-[10px]">
          <span className="flex items-center gap-1 text-ink-200">
            <span className="inline-block h-2 w-2 rounded-full bg-bad-500 ring-1 ring-white" />high
          </span>
          <span className="flex items-center gap-1 text-ink-200">
            <span className="inline-block h-2 w-2 rounded-full bg-warn-500 ring-1 ring-white" />med
          </span>
          <span className="flex items-center gap-1 text-ink-200">
            <span className="inline-block h-2 w-2 rounded-full bg-good-500 ring-1 ring-white" />low
          </span>
        </div>
        <div className="mt-0.5 text-[8px] text-ink-500">
          ▲ incidents · ● police · ─ diversions
        </div>
      </div>

      {/* pulse-ring CSS keyframes (one place, not duplicated) */}
      <style>{`
        @keyframes gl-pulse-ring {
          0%   { r: 12; stroke-opacity: 0.9; }
          100% { r: 60; stroke-opacity: 0;  }
        }
        @keyframes gl-pulse-glow {
          0%, 100% { filter: drop-shadow(0 0 0   rgba(168, 85, 247, 0)); }
          50%      { filter: drop-shadow(0 0 6px rgba(168, 85, 247, 0.6)); }
        }
      `}</style>
    </div>
  );
}
