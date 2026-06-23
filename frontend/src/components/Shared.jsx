// Tiny shared UI bits. Kept inline so the SPA stays a single deployable
// static bundle (no heavy component library) — the spec calls for
// "5 views, not a heavy TS monorepo".

import { Link, NavLink, useLocation } from "react-router-dom";
import { useEffect, useState } from "react";
import { HelpCircle } from "lucide-react";
import { api, ApiError } from "../lib/api.js";
import { TOUR_EVENT } from "../lib/tourSteps.js";

const NAV = [
  { to: "/",           label: "Live",     icon: "🛰" },
  { to: "/predict",    label: "Predict",  icon: "🎯" },
  { to: "/allocate",   label: "Allocate", icon: "⚖" },
  { to: "/simulate",   label: "Simulate", icon: "▦" },
  { to: "/debrief",    label: "Debrief",  icon: "📈" },
];

export function Header() {
  const [health, setHealth] = useState(null);
  useEffect(() => {
    let alive = true;
    api.health()
      .then((h) => { if (alive) setHealth(h); })
      .catch((e) => { if (alive) setHealth({ status: "down", error: String(e) }); });
    const t = setInterval(() => {
      api.health().then((h) => setHealth(h)).catch(() => {});
    }, 15000);
    return () => { alive = false; clearInterval(t); };
  }, []);

  return (
    <header data-tour="header" className="sticky top-0 z-30 shrink-0 border-b border-ink-800 bg-ink-950/80 backdrop-blur">
      <div className="flex w-full items-center gap-4 px-4 py-3">
        <Link to="/" className="flex items-center gap-2">
          <div className="grid h-7 w-7 place-items-center rounded-md bg-accent-600 text-white shadow-glow">
            <span className="text-sm font-bold">G</span>
          </div>
          <div className="flex flex-col leading-tight">
            <div className="text-sm font-semibold text-ink-50">NexGen</div>
            <div className="text-[10px] uppercase tracking-wider text-ink-400">
              ASTraM intelligence layer
            </div>
          </div>
        </Link>
        <nav data-tour="nav" className="ml-2 flex items-center gap-1">
          {NAV.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              end={n.to === "/"}
              className={({ isActive }) =>
                `flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-sm ` +
                (isActive
                  ? "bg-ink-800 text-ink-50"
                  : "text-ink-300 hover:bg-ink-800 hover:text-ink-100")
              }
            >
              <span className="text-base leading-none">{n.icon}</span>
              <span>{n.label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="ml-auto flex items-center gap-3">
          <HealthPill health={health} />
          <HelpButton />
          <RoleToggle />
        </div>
      </div>
    </header>
  );
}

function HelpButton() {
  return (
    <button
      onClick={() => window.dispatchEvent(new CustomEvent(TOUR_EVENT))}
      aria-label="Start onboarding tour"
      title="Take the tour"
      className="grid h-7 w-7 place-items-center rounded-md border border-ink-700 bg-ink-900 text-ink-300 transition hover:border-ink-600 hover:bg-ink-800 hover:text-ink-50"
    >
      <HelpCircle size={14} />
    </button>
  );
}

function HealthPill({ health }) {
  if (!health) {
    return <span className="pill-ink">connecting…</span>;
  }
  const ok = health.status === "ok";
  return (
    <div className="flex items-center gap-2 text-[11px] text-ink-400">
      <span className={`h-1.5 w-1.5 rounded-full ${ok ? "bg-good-500" : "bg-bad-500"} animate-pulse`} />
      <span className="font-mono">
        {ok ? "API ok" : "API down"} · {health.n_corridors ?? "–"} corridors
        · {health.n_cascade_edges ?? "–"} edges
      </span>
    </div>
  );
}

function RoleToggle() {
  const [role, setRole] = useState(() => localStorage.getItem("role") || "controller");
  useEffect(() => {
    localStorage.setItem("role", role);
    window.dispatchEvent(new CustomEvent("role-change", { detail: role }));
  }, [role]);
  return (
    <div className="flex items-center rounded-md border border-ink-700 bg-ink-900 p-0.5 text-[11px]">
      {["controller", "fleet"].map((r) => (
        <button
          key={r}
          onClick={() => setRole(r)}
          className={
            "rounded px-2 py-1 capitalize transition " +
            (role === r
              ? "bg-accent-600 text-white"
              : "text-ink-300 hover:text-ink-100")
          }
        >
          {r}
        </button>
      ))}
    </div>
  );
}

// ----------------------- shared building blocks -----------------------
export function ConfidenceChip({ confidence }) {
  const klass = {
    high:   "pill-good",
    medium: "pill-warn",
    low:    "pill-bad",
  }[confidence] || "pill-ink";
  return (
    <span className={klass}>
      <span className="text-[10px]">●</span>
      {confidence || "unknown"} confidence
    </span>
  );
}

export function TierChip({ tier }) {
  const klass = {
    HIGH: "pill-bad",
    MED:  "pill-warn",
    LOW:  "pill-good",
  }[tier] || "pill-ink";
  return <span className={klass}>{tier || "–"} tier</span>;
}

export function MetricCard({ label, value, sub, accent, onClick, active }) {
  return (
    <div
      onClick={onClick}
      className={
        "card p-3 transition " +
        (onClick ? "cursor-pointer hover:border-accent-500/60 " : "") +
        (active ? "border-accent-500 bg-accent-500/10" : "")
      }
    >
      <div className="label">{label}</div>
      <div className={"mt-1 stat " + (accent || active ? "text-accent-400" : "text-ink-50")}>
        {value}
      </div>
      {sub ? <div className="mt-0.5 text-[11px] text-ink-400">{sub}</div> : null}
    </div>
  );
}

export function ErrorPanel({ error }) {
  if (!error) return null;
  return (
    <div className="card border-bad-500/30 bg-bad-500/5 p-3 text-sm text-bad-500">
      <div className="font-medium">Something went wrong</div>
      <div className="mt-1 text-bad-500/80">{String(error)}</div>
    </div>
  );
}

export function Loading({ label = "Loading…" }) {
  return (
    <div className="flex items-center gap-2 text-sm text-ink-400">
      <span className="h-2 w-2 animate-pulse rounded-full bg-accent-500" />
      {label}
    </div>
  );
}

export function Band({ p10, p50, p90, cap = 360 }) {
  // Visualizes the [P10, P90] clearance band as a horizontal track
  // with P50 marker — the spec's confidence-gated UI hook.
  const scale = (v) => Math.max(0, Math.min(100, (v / cap) * 100));
  const lo = scale(p10), mid = scale(p50), hi = scale(p90);
  return (
    <div className="space-y-1">
      <div className="relative h-2 rounded-full bg-ink-800">
        <div
          className="absolute h-2 rounded-full bg-gradient-to-r from-accent-700 via-accent-500 to-accent-300"
          style={{ left: `${lo}%`, width: `${Math.max(2, hi - lo)}%` }}
        />
        <div
          className="absolute -top-1 h-4 w-0.5 rounded bg-white shadow-glow"
          style={{ left: `${mid}%` }}
        />
      </div>
      <div className="flex justify-between text-[10px] text-ink-400 font-mono">
        <span>P10 {p10}m</span>
        <span className="text-ink-200">P50 {p50}m</span>
        <span>P90 {p90}m</span>
      </div>
    </div>
  );
}

export function PageHeader({ title, subtitle, actions }) {
  return (
    <div className="mb-4 flex flex-wrap items-end justify-between gap-2">
      <div>
        <h1 className="text-xl font-semibold text-ink-50">{title}</h1>
        {subtitle ? (
          <p className="mt-0.5 text-sm text-ink-400">{subtitle}</p>
        ) : null}
      </div>
      {actions ? <div className="flex items-center gap-2">{actions}</div> : null}
    </div>
  );
}

export function useRole() {
  const [role, setRole] = useState(() => localStorage.getItem("role") || "controller");
  useEffect(() => {
    const cb = (e) => setRole(e.detail);
    window.addEventListener("role-change", cb);
    return () => window.removeEventListener("role-change", cb);
  }, []);
  return role;
}
