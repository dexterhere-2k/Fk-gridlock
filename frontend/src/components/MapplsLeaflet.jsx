// Vanilla Leaflet map for /plan — NO react-leaflet.
// Uses native L.map()/L.tileLayer()/L.marker()/L.polyline().
// react-leaflet was incompatible with our React 18 + Vite setup;
// vanilla Leaflet is the only reliable approach.
import { useEffect, useMemo, useRef, useState } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";

const BLR_CENTER = [12.9716, 77.5946];
const TILE_DARK  = "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png";
const TILE_LIGHT = "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png";

function congestionColor(c) {
  if (c < 20) return "#34d399"; if (c < 40) return "#a3e635";
  if (c < 60) return "#facc15"; if (c < 75) return "#fb923c";
  if (c < 90) return "#e85d04"; return "#dc2626";
}

const ICON_CACHE = new Map();
function junctionIcon(cong) {
  const c = congestionColor(cong);
  const t = Math.min(Math.max(cong, 0), 100) / 100;
  const d = Math.round((6 + t * 8) * 10) / 10;
  const k = `${c}|${d}`;
  if (!ICON_CACHE.has(k)) {
    ICON_CACHE.set(k, L.divIcon({
      className: "junction-label",
      html: `<div class="jnode-flat" style="--c:${c};--d:${d.toFixed(1)}px"></div>`,
      iconSize: [d, d], iconAnchor: [d / 2, d / 2],
    }));
  }
  return ICON_CACHE.get(k);
}

const VENUE_ICON = L.divIcon({
  className: "junction-label",
  html: `<div class="venue-pin">★</div>`,
  iconSize: [26, 26], iconAnchor: [13, 13],
});
const BARRICADE_ICON = L.divIcon({
  className: "junction-label",
  html: `<div class="barricade-pin">▣</div>`,
  iconSize: [20, 20], iconAnchor: [10, 10],
});

const officerBadgeIcon = (n) => L.divIcon({
  className: "junction-label",
  html: `<div class="officer-badge">${n}</div>`,
  iconSize: [13, 13], iconAnchor: [6.5, 6.5],
});
const officerClusterIcon = (n) => L.divIcon({
  className: "junction-label",
  html: `<div class="officer-cluster">${n}</div>`,
  iconSize: [18, 18], iconAnchor: [9, 9],
});

export default function MapplsLeaflet({
  forecast, barricades = [], diversions = [], officers = [],
  timeIndex = 0, loading = false, fitVersion = 0,
  forceDarkTiles = false,
}) {
  const containerRef = useRef(null);
  const mapRef = useRef(null);
  const layersRef = useRef({ junctions: [], officers: [], barricades: [], diversions: [], venue: null });
  const [inited, setInited] = useState(false);

  const isDark = forceDarkTiles || (
    (typeof document !== "undefined" && document.documentElement.classList.contains("dark"))
  );
  const bucket = forecast?.timeline?.[timeIndex];

  // --- Map init (once) ---
  useEffect(() => {
    if (mapRef.current || !containerRef.current) return;
    const map = L.map(containerRef.current, {
      center: BLR_CENTER, zoom: 12, zoomControl: true,
    });
    L.tileLayer(isDark ? TILE_DARK : TILE_LIGHT, {
      subdomains: "abcd", maxZoom: 19,
    }).addTo(map);

    // ResizeObserver for flex layout
    const ro = new ResizeObserver(() => map.invalidateSize());
    ro.observe(containerRef.current);
    mapRef.current = map;
    setInited(true);

    return () => { ro.disconnect(); map.remove(); mapRef.current = null; };
  }, []);

  // --- Theme change — swap tiles ---
  useEffect(() => {
    const cb = () => {
      if (!mapRef.current) return;
      mapRef.current.eachLayer((ly) => {
        if (ly instanceof L.TileLayer) mapRef.current.removeLayer(ly);
      });
      L.tileLayer(isDark ? TILE_DARK : TILE_LIGHT, {
        subdomains: "abcd", maxZoom: 19,
      }).addTo(mapRef.current);
    };
    window.addEventListener("theme-change", cb);
    return () => window.removeEventListener("theme-change", cb);
  }, [isDark]);

  // --- Fit to data ---
  useEffect(() => {
    if (!mapRef.current || !forecast || !fitVersion) return;
    const pts = forecast.perJunction.map((p) => [p.lat, p.lng]);
    if (pts.length < 2) return;
    mapRef.current.fitBounds(L.latLngBounds(pts).pad(0.2), { animate: true, maxZoom: 12 });
  }, [forecast, fitVersion]);

  // --- Data layers (re-render when forecast/officers/diversions/barricades change) ---
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !inited) return;

    // Clear old layers
    const l = layersRef.current;
    l.junctions.forEach((m) => map.removeLayer(m));
    l.officers.forEach((m) => map.removeLayer(m));
    l.barricades.forEach((m) => map.removeLayer(m));
    l.diversions.forEach((m) => map.removeLayer(m));
    if (l.venue) map.removeLayer(l.venue);
    l.junctions = []; l.officers = []; l.barricades = []; l.diversions = []; l.venue = null;

    // Junctions
    const perJ = forecast?.perJunction ?? [];
    perJ.forEach((p) => {
      const cong = bucket ? (bucket.congestion[p.id] ?? p.congestion) : p.congestion;
      const delta = bucket ? (bucket.delta[p.id] ?? p.delta) : p.delta;
      const m = L.marker([p.lat, p.lng], { icon: junctionIcon(cong) })
        .addTo(map)
        .bindTooltip(`<b>${p.name}</b><br>Congestion: <b style="color:${congestionColor(cong)}">${cong.toFixed(0)}/100</b><br>Event impact: +${delta.toFixed(0)} pts<br>Avg delay: ${p.delay.toFixed(1)} min`);
      l.junctions.push(m);
    });

    // Officers
    (forecast ? officers : []).forEach((o) => {
      const m = L.marker([o.lat, o.lng], { icon: officerBadgeIcon(o.officers) })
        .addTo(map)
        .bindTooltip(`${o.junctionName}: ${o.officers} officers`);
      l.officers.push(m);
    });

    // Barricades — pin markers only, no line overlay
    (forecast ? barricades : []).forEach((b) => {
      const mid = [(b.route[0][0] + b.route[b.route.length-1][0]) / 2,
                    (b.route[0][1] + b.route[b.route.length-1][1]) / 2];
      const pin = L.marker(mid, { icon: BARRICADE_ICON })
        .addTo(map)
        .bindTooltip(`<b>${b.action}</b><br>${b.road}`);
      l.barricades.push(pin);
    });

    // Diversions — only the red suggested route (no grey original)
    (forecast ? diversions : []).forEach((d) => {
      const sug = L.polyline(d.suggestedRoute, {
        color: "#e85d04", weight: 4.5, opacity: 0.95, className: "diversion-flow",
      }).addTo(map);
      sug.bindTooltip(`<b>Diversion:</b> ${d.from} → ${d.to}<br>Avoids: ${d.avoids.join(", ")}<br>${d.normalTimeMin} min → ${d.divertedTimeMin} min`);
      l.diversions.push(sug);
    });

    // Venue
    if (forecast?.event) {
      const v = forecast.event;
      l.venue = L.marker([v.venueLat, v.venueLng], { icon: VENUE_ICON })
        .addTo(map)
        .bindTooltip(`★ ${v.venueName}`, { permanent: true, direction: "top" });
    }
  }, [forecast, officers, barricades, diversions, bucket, inited]);

  return (
    <div className="relative h-full w-full">
      <div ref={containerRef} className="h-full w-full" />

      {loading && (
        <div className="pointer-events-none absolute inset-0 z-[700] flex items-center justify-center" style={{ background: "rgba(0,0,0,0.35)" }}>
          <div className="rounded-xl border px-4 py-2.5 text-[12px] font-extrabold shadow-lg" style={{ borderColor: "var(--border)", background: "var(--panel)", color: "var(--foreground)" }}>
            <span className="inline-block animate-pulse">●</span> Running scenario…
          </div>
        </div>
      )}
      {!forecast && !loading && (
        <div className="pointer-events-none absolute inset-0 z-[500] flex items-center justify-center">
          <div className="rounded-xl border px-4 py-2.5 text-[12px] font-semibold" style={{ borderColor: "var(--border)", background: "var(--panel)", color: "var(--muted)" }}>
            Configure a scenario and hit <span style={{ color: "var(--accent)" }}>Run forecast &amp; plan</span>
          </div>
        </div>
      )}
    </div>
  );
}
