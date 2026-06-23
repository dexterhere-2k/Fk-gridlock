// 6-tile impact summary bar for the simulate command-center.
import {
  AlertTriangle, Clock, Radius, Shield, TrafficCone, Users,
} from "lucide-react";

function Stat({ label, value, sub, icon }) {
  return (
    <div className="card-tight px-3 py-2.5">
      <div className="flex items-center gap-1.5 text-[11px] uppercase tracking-wide"
           style={{ color: "var(--muted)" }}>
        {icon && <span style={{ opacity: 0.7 }}>{icon}</span>}
        <span>{label}</span>
      </div>
      <div className="mt-1 text-[22px] font-bold leading-none tracking-tight"
           style={{ color: "var(--foreground)" }}>
        {value}
      </div>
      {sub && (
        <div className="mt-1 text-[11px]" style={{ color: "var(--muted)" }}>
          {sub}
        </div>
      )}
    </div>
  );
}

function congestionColor(c) {
  if (c < 20) return "#34d399";
  if (c < 40) return "#a3e635";
  if (c < 60) return "#facc15";
  if (c < 75) return "#fb923c";
  if (c < 90) return "#e85d04";
  return "#e11d48";
}

export default function PlanKpis({ kpis, plan }) {
  if (!kpis || !plan) return null;
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-6">
      <Stat
        label="Peak congestion"
        value={<span style={{ color: congestionColor(kpis.peakCongestion) }}>
          {kpis.peakCongestion.toFixed(0)}
        </span>}
        sub={`${kpis.peakPhase} · ${kpis.peakTimeLabel}`}
        icon={<AlertTriangle size={12} />}
      />
      <Stat
        label="Worst corridor"
        value={
          <span className="text-[15px] font-semibold leading-tight">
            {kpis.worstJunction ?? "—"}
          </span>
        }
        sub={`${kpis.avgDelayAtPeak.toFixed(1)} min avg delay`}
        icon={<TrafficCone size={12} />}
      />
      <Stat
        label="Corridors hit"
        value={kpis.junctionsAffected}
        sub="event-attributable surge"
        icon={<Radius size={12} />}
      />
      <Stat
        label="Impact radius"
        value={
          <>
            {kpis.impactRadiusKm.toFixed(1)}
            <span className="text-base" style={{ color: "var(--muted)" }}> km</span>
          </>
        }
        sub="from venue"
        icon={<Clock size={12} />}
      />
      <Stat
        label="Officers"
        value={plan.summary.officersDeployed}
        sub={`${plan.summary.junctionsStaffed} corridors staffed`}
        icon={<Users size={12} />}
      />
      <Stat
        label="Interventions"
        value={
          <span>
            {plan.summary.barricadePoints}
            <span className="text-base" style={{ color: "var(--muted)" }}> bar</span> ·{" "}
            {plan.summary.diversionRoutes}
            <span className="text-base" style={{ color: "var(--muted)" }}> div</span>
          </span>
        }
        sub="barricades · diversions"
        icon={<Shield size={12} />}
      />
    </div>
  );
}
