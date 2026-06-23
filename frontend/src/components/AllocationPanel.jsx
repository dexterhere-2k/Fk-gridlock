// Allocation panel for the simulate command-center.
// 3 tabs: Manpower / Barricades / Diversions with a per-tab count badge,
// a per-tab visual mitigation bar, and an export-to-markdown button.
import { useState } from "react";
import {
  ArrowRight, Route, TrafficCone, Users, Download,
} from "lucide-react";

function Empty({ text }) {
  return (
    <div className="rounded-xl border border-dashed px-3 py-6 text-center text-[12px] font-semibold"
         style={{ borderColor: "var(--border)", background: "var(--panel-2)",
                  color: "var(--muted)" }}>
      {text}
    </div>
  );
}

function exportPlan(plan) {
  let txt = `# GRIDLOCK DEPLOYMENT PLAN\n\n`;
  txt += `Summary: ${plan.summary.officersDeployed} officers deployed across ${plan.summary.junctionsStaffed} corridors. `;
  txt += `${plan.summary.barricadePoints} barricades and ${plan.summary.diversionRoutes} diversions recommended.\n\n`;

  txt += `## 1. MANPOWER ALLOCATION\n\n`;
  if (plan.manpower.officers.length === 0) {
    txt += `No officers required.\n\n`;
  } else {
    plan.manpower.officers.forEach((o) => {
      txt += `- **Corridor:** ${o.junctionName}\n`;
      txt += `  - Officers Deployed: ${o.officers}\n`;
      txt += `  - Expected Delay: ${o.expectedDelayBefore.toFixed(1)}m → ${o.expectedDelayAfter.toFixed(1)}m (${o.mitigationPct}% relief)\n`;
      txt += `  - Reason: ${o.reason}\n\n`;
    });
  }

  txt += `## 2. BARRICADES\n\n`;
  if (plan.barricades.length === 0) {
    txt += `No barricades recommended.\n\n`;
  } else {
    plan.barricades.forEach((b) => {
      txt += `- **Location:** ${b.road} (${b.from} → ${b.to})\n`;
      txt += `  - Action: ${b.action}\n`;
      txt += `  - Impact Score: +${b.impact}\n`;
      txt += `  - Reason: ${b.reason}\n\n`;
    });
  }

  txt += `## 3. DIVERSIONS\n\n`;
  if (plan.diversions.length === 0) {
    txt += `No diversions needed.\n\n`;
  } else {
    plan.diversions.forEach((d) => {
      txt += `- **Reroute:** ${d.from} → ${d.to}\n`;
      txt += `  - Avoids: ${d.avoids.join(", ")}\n`;
      txt += `  - Travel Time: ${d.normalTimeMin.toFixed(0)}m → ${d.divertedTimeMin.toFixed(0)}m via diversion\n`;
      txt += `  - Reason: ${d.reason}\n\n`;
    });
  }

  const blob = new Blob([txt], { type: "text/markdown;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.setAttribute("href", url);
  link.setAttribute("download", `nexgen_deployment_plan.md`);
  link.style.visibility = "hidden";
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
}

export default function PlanRecommendations({ plan }) {
  const [tab, setTab] = useState("manpower");
  if (!plan) return null;

  const tabs = [
    { key: "manpower",  label: "Manpower",  count: plan.manpower.junctionsStaffed, icon: <Users size={13} /> },
    { key: "barricades", label: "Barricades", count: plan.barricades.length,         icon: <TrafficCone size={13} /> },
    { key: "diversions", label: "Diversions", count: plan.diversions.length,         icon: <Route size={13} /> },
  ];

  return (
    <div className="flex h-full flex-col">
      <div className="mb-3 flex items-center justify-between">
        <div className="flex gap-1.5">
          {tabs.map((t) => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className="flex items-center gap-1.5 rounded-xl px-2.5 py-1.5 text-[12px] font-bold transition cursor-pointer"
              style={{
                background: tab === t.key ? "var(--accent)" : "transparent",
                color: tab === t.key ? "var(--accent-foreground)" : "var(--muted)",
                boxShadow: tab === t.key ? "0 1px 3px rgba(0,0,0,0.15)" : "none",
              }}
            >
              {t.icon}
              {t.label}
              <span
                className="rounded-full px-1.5 py-0.5 text-[10px] font-bold"
                style={{
                  background: tab === t.key
                    ? "rgba(255,255,255,0.2)"
                    : "var(--panel-2)",
                  color: tab === t.key ? "var(--accent-foreground)" : "var(--muted)",
                  border: tab === t.key ? "none" : "1px solid var(--border)",
                }}
              >
                {t.count}
              </span>
            </button>
          ))}
        </div>
        <button
          onClick={() => exportPlan(plan)}
          className="flex items-center gap-1.5 rounded-xl border px-2.5 py-1.5 text-[11px] font-bold transition cursor-pointer"
          style={{
            borderColor: "var(--border)",
            background: "var(--panel)",
            color: "var(--muted)",
          }}
          title="Export deployment plan"
        >
          <Download size={12} /> Export
        </button>
      </div>

      <div className="flex-1 overflow-y-auto pr-1" style={{ scrollbarWidth: "thin" }}>
        {tab === "manpower" && (
          <div className="flex flex-col gap-2">
            {plan.manpower.officers.length === 0 && (
              <Empty text="No officers required — negligible event impact." />
            )}
            {plan.manpower.officers.map((o) => (
              <div key={o.junctionId} className="card-tight px-3.5 py-3 flex flex-col gap-2"
                   style={{ borderColor: "var(--border)" }}>
                <div className="flex items-center justify-between">
                  <span className="text-[13px] font-extrabold tracking-tight"
                        style={{ color: "var(--foreground)" }}>
                    {o.junctionName}
                  </span>
                  <span className="rounded-full border px-2.5 py-0.5 text-[11px] font-extrabold"
                        style={{ borderColor: "var(--border)",
                                 background: "var(--panel-2)",
                                 color: "var(--muted)" }}>
                    <Users size={11} className="mr-1 inline-block" /> {o.officers} officers
                  </span>
                </div>
                <div className="flex items-center justify-between text-[11px] font-bold"
                     style={{ color: "var(--muted)" }}>
                  <div className="flex items-center gap-1.5">
                    <span className="line-through tabular-nums">{o.expectedDelayBefore.toFixed(1)}m</span>
                    <ArrowRight size={10} />
                    <span className="font-extrabold tabular-nums"
                          style={{ color: "var(--foreground)" }}>
                      {o.expectedDelayAfter.toFixed(1)}m delay
                    </span>
                  </div>
                  <span className="font-extrabold" style={{ color: "var(--foreground)" }}>
                    {o.mitigationPct}% relief
                  </span>
                </div>
                <div className="h-1 w-full rounded-full overflow-hidden"
                     style={{ background: "var(--border)" }}>
                  <div
                    className="h-full rounded-full transition-all duration-500"
                    style={{
                      width: `${o.mitigationPct}%`,
                      background: "var(--accent)",
                    }}
                  />
                </div>
                <p className="text-[11px] leading-relaxed font-medium mt-0.5"
                   style={{ color: "var(--muted)" }}>
                  {o.reason}
                </p>
              </div>
            ))}
          </div>
        )}

        {tab === "barricades" && (
          <div className="flex flex-col gap-2">
            {plan.barricades.length === 0 && (
              <Empty text="No barricades recommended." />
            )}
            {plan.barricades.map((b, i) => (
              <div key={i} className="card-tight px-3.5 py-3 flex flex-col gap-1.5">
                <div className="flex items-center justify-between">
                  <span className="text-[13px] font-extrabold tracking-tight"
                        style={{ color: "var(--foreground)" }}>
                    {b.road}
                  </span>
                  <span className="rounded-full border px-2.5 py-0.5 text-[11px] font-extrabold"
                        style={{ borderColor: "var(--border)",
                                 background: "var(--panel-2)",
                                 color: "var(--muted)" }}>
                    +{b.impact} impact
                  </span>
                </div>
                <div className="flex items-center gap-1.5 text-[11px] font-bold"
                     style={{ color: "var(--muted)" }}>
                  <span>{b.from}</span>
                  <ArrowRight size={10} />
                  <span>{b.to}</span>
                </div>
                <div className="mt-0.5">
                  <span className="inline-block px-1.5 py-0.5 rounded-md text-[10px] font-extrabold uppercase tracking-wider"
                        style={{ background: "var(--panel-2)",
                                 color: "var(--foreground)" }}>
                    {b.action}
                  </span>
                </div>
                <p className="text-[11px] leading-relaxed font-medium mt-0.5"
                   style={{ color: "var(--muted)" }}>
                  {b.reason}
                </p>
              </div>
            ))}
          </div>
        )}

        {tab === "diversions" && (
          <div className="flex flex-col gap-2">
            {plan.diversions.length === 0 && (
              <Empty text="No diversions needed — through-traffic unaffected." />
            )}
            {plan.diversions.map((d, i) => {
              const diff = d.divertedTimeMin - d.normalTimeMin;
              const increasePct = d.normalTimeMin > 0
                ? Math.round((diff / d.normalTimeMin) * 100) : 0;
              return (
                <div key={i} className="card-tight px-3.5 py-3 flex flex-col gap-1.5">
                  <div className="flex items-center gap-1.5 text-[13px] font-extrabold tracking-tight"
                       style={{ color: "var(--foreground)" }}>
                    {d.from}
                    <ArrowRight size={12} className="inline-block"
                                style={{ color: "var(--muted)" }} />
                    {d.to}
                  </div>
                  <div className="flex items-center justify-between text-[11px] font-bold mt-0.5"
                       style={{ color: "var(--muted)" }}>
                    <div className="flex items-center gap-2">
                      <span className="line-through tabular-nums">{d.normalTimeMin.toFixed(0)}m normal</span>
                      <ArrowRight size={10} />
                      <span className="font-extrabold tabular-nums"
                            style={{ color: "var(--foreground)" }}>
                        {d.divertedTimeMin.toFixed(0)}m via route
                      </span>
                    </div>
                    {diff > 0 ? (
                      <span>+{diff.toFixed(0)}m ({increasePct}%)</span>
                    ) : (
                      <span>no delay</span>
                    )}
                  </div>
                  <div className="h-1 w-full rounded-full overflow-hidden flex"
                       style={{ background: "var(--border)" }}>
                    <div
                      className="h-full transition-all duration-500"
                      style={{
                        width: `${Math.min(100, (d.normalTimeMin / Math.max(d.normalTimeMin, d.divertedTimeMin)) * 100)}%`,
                        background: "var(--muted)",
                        opacity: 0.5,
                      }}
                    />
                    {diff > 0 && (
                      <div
                        className="h-full transition-all duration-500"
                        style={{
                          width: `${(diff / Math.max(d.normalTimeMin, d.divertedTimeMin)) * 100}%`,
                          background: "var(--accent)",
                        }}
                      />
                    )}
                  </div>
                  <div className="text-[11px] font-medium mt-1 leading-relaxed"
                       style={{ color: "var(--muted)" }}>
                    Keeps clear:{" "}
                    <span className="font-extrabold"
                          style={{ color: "var(--foreground)" }}>
                      {d.avoids.join(", ")}
                    </span>
                  </div>
                  <p className="text-[11px] leading-relaxed font-medium mt-0.5"
                     style={{ color: "var(--muted)" }}>
                    {d.reason}
                  </p>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
