// Vanilla Leaflet map for Live view — NO react-leaflet, NO pure SVG.
// Shows corridor risk heatmap, cascade overlay, incident pins, WS pulses.
import { useEffect, useMemo, useRef, useState } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";

const BLR_CENTER = [12.9716, 77.5946];
const TILE_URL = "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png";

function riskColor(score) {
  if (score > 0.6) return "#dc2626";
  if (score > 0.3) return "#f59e0b";
  return "#16a34a";
}

function heatIcon(score) {
  const c = riskColor(score);
  const size = 8 + Math.min(1, score) * 8;
  return L.divIcon({
    className: "junction-label",
    html: `<div style="width:${size}px;height:${size}px;border-radius:50%;background:${c};box-shadow:0 0 ${size/2}px ${c};opacity:0.9"></div>`,
    iconSize: [size, size], iconAnchor: [size / 2, size / 2],
  });
}

export default function MapplsMap({
  corridors = [], incidents = [], stations = [], diversions = [],
  highlight = null, pulseCorridor = null, onCorridorClick,
}) {
  const containerRef = useRef(null);
  const mapRef = useRef(null);
  const markersRef = useRef([]);

  // --- Map init ---
  useEffect(() => {
    if (mapRef.current || !containerRef.current) return;
    const map = L.map(containerRef.current, {
      center: BLR_CENTER, zoom: 11, zoomControl: true,
    });
    L.tileLayer(TILE_URL, { subdomains: "abcd", maxZoom: 19 }).addTo(map);
    const ro = new ResizeObserver(() => map.invalidateSize());
    ro.observe(containerRef.current);
    mapRef.current = map;
    return () => { ro.disconnect(); map.remove(); mapRef.current = null; };
  }, []);

  // --- Data layers ---
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    // Clear old markers
    markersRef.current.forEach((m) => map.removeLayer(m));
    markersRef.current = [];

    // Corridor heatmap dots
    corridors.forEach((c) => {
      const lat = c.lat || (BLR_CENTER[0] + (Math.random() - 0.5) * 0.1);
      const lng = c.lon || (BLR_CENTER[1] + (Math.random() - 0.5) * 0.15);
      const score = c.risk_score || 0.3;
      const isPulse = pulseCorridor && c.corridor === pulseCorridor;
      const icon = L.divIcon({
        className: "junction-label",
        html: `<div style="width:${10 + score * 16}px;height:${10 + score * 16}px;border-radius:50%;background:${riskColor(score)};box-shadow:0 0 ${6 + score * 10}px ${riskColor(score)};opacity:${isPulse ? 1 : 0.85};${isPulse ? 'animation:pulse-ring-cf 1.5s ease-out infinite' : ''}"></div>`,
        iconSize: [10 + score * 16, 10 + score * 16],
        iconAnchor: [(10 + score * 16) / 2, (10 + score * 16) / 2],
      });
      const m = L.marker([lat, lng], { icon, zIndexOffset: Math.round(score * 100) })
        .addTo(map)
        .bindTooltip(`<b>${c.corridor}</b><br>Events: ${c.events || "–"}<br>Risk: ${score.toFixed(2)}`);
      if (onCorridorClick) m.on("click", () => onCorridorClick(c));
      markersRef.current.push(m);
    });

    // Police stations
    stations.forEach((s) => {
      const lat = s.lat || BLR_CENTER[0];
      const lng = s.lon || BLR_CENTER[1];
      const m = L.marker([lat, lng], {
        icon: L.divIcon({
          html: '<div style="width:10px;height:10px;background:#1f7af8;border-radius:2px;box-shadow:0 0 6px #1f7af8"></div>',
          iconSize: [10, 10], iconAnchor: [5, 5],
        }),
      }).addTo(map).bindTooltip(s.name || "Station");
      markersRef.current.push(m);
    });

    // Diversion polylines
    diversions.forEach((d) => {
      if (d.polyline && d.polyline.length > 1) {
        const pts = d.polyline.map((p) => [p.lat, p.lon]);
        const poly = L.polyline(pts, {
          color: d.source_kind === "cascade" ? "#facc15" : "#e85d04",
          weight: d.source_kind === "cascade" ? 5 : 3, opacity: 0.9, dashArray: d.source_kind === "cascade" ? "6 4" : undefined,
          className: d.source_kind !== "cascade" ? "diversion-flow" : undefined,
        }).addTo(map);
        poly.bindTooltip(`${d.source || ""} → ${d.target || ""}${d.eta_min ? ` · ${d.eta_min}m` : ""}`);
        markersRef.current.push(poly);
      }
    });

    // Highlight corridor if selected
    if (highlight) {
      const hc = corridors.find((c) => c.corridor === highlight);
      if (hc) {
        const lat = hc.lat || BLR_CENTER[0];
        const lng = hc.lon || BLR_CENTER[1];
        const ring = L.circle([lat, lng], {
          radius: 800, color: "#e85d04", weight: 2, fill: false, dashArray: "4 4",
        }).addTo(map);
        markersRef.current.push(ring);
      }
    }
  }, [corridors, stations, diversions, highlight, pulseCorridor]);

  return <div ref={containerRef} className="h-full w-full" />;
}
