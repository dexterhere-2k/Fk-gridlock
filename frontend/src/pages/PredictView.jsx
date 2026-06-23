// View 2 — Unplanned incident → clearance range + confidence + recommended
// officers. Includes a Kannada demo (spec 02 §2b).

import { useState } from "react";
import { api } from "../lib/api.js";
import {
  Band, ConfidenceChip, TierChip, MetricCard, ErrorPanel,
  Loading, PageHeader,
} from "../components/Shared.jsx";

const SAMPLES = [
  {
    name: "Tree fall (English)",
    payload: {
      corridor: "Bellary Road 1", event_cause: "tree_fall",
      description: "huge tree fallen blocking the road crane needed",
      datetime: "2024-04-01T05:00:00+05:30",
    },
  },
  {
    name: "Vehicle breakdown (English)",
    payload: {
      corridor: "Mysore Road", event_cause: "vehicle_breakdown",
      veh_type: "lcv", description: "lcv breakdown near yeshwantpur",
      datetime: "2024-04-01T18:00:00+05:30",
    },
  },
  {
    name: "Kannada bus off road ⭐",
    payload: {
      corridor: "Tumkur Road", event_cause: "vehicle_breakdown",
      description: "ನಮಸ್ತೆ ಸರ್ ಬಸ್ ಆಫ್ ರೋಡ್ ಆಗಿರುತ್ತದೆ ಕ್ರೇನ್ ಬೇಕು ಒಂದು ಲೇನ್ ಬ್ಲಾಕ್",
      datetime: "2024-04-01T20:00:00+05:30",
    },
  },
  {
    name: "VIP convoy (planned)",
    payload: {
      corridor: "Mysore Road", event_cause: "vip_movement",
      is_planned: true, event_type: "planned",
      description: "vip convoy passing through",
      datetime: "2024-04-01T15:00:00+05:30",
    },
  },
];

export default function PredictView() {
  const [form, setForm] = useState(SAMPLES[2].payload);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);

  const run = async () => {
    setLoading(true); setError(null); setResult(null);
    try {
      const r = await api.clearanceRisk(form);
      setResult(r);
    } catch (e) { setError(e); }
    finally { setLoading(false); }
  };

  return (
    <div className="flex h-full min-h-0 flex-col gap-2 overflow-y-auto p-3">
      <PageHeader
        title="Predict clearance & closure"
        subtitle="Single-incident clearance range (P10/P50/P90) + closure probability + NLP-parsed cues"
      />
      <ErrorPanel error={error} />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-5">
        <section data-tour="predict-form" className="card p-4 lg:col-span-2">
          <h2 className="mb-2 text-sm font-semibold text-ink-100">Event input</h2>
          <div className="mb-3 flex flex-wrap gap-1.5">
            {SAMPLES.map((s) => (
              <button
                key={s.name}
                onClick={() => setForm(s.payload)}
                className="rounded-full border border-ink-700 bg-ink-900 px-2.5 py-1 text-[11px] text-ink-200 hover:bg-ink-800"
              >
                {s.name}
              </button>
            ))}
          </div>
          <div className="space-y-2">
            <Field label="corridor">
              <input className="input" value={form.corridor}
                     onChange={(e) => setForm({ ...form, corridor: e.target.value })} />
            </Field>
            <Field label="event_cause">
              <input className="input" value={form.event_cause}
                     onChange={(e) => setForm({ ...form, event_cause: e.target.value })} />
            </Field>
            <Field label="description (free text, Kannada OK)">
              <textarea className="textarea min-h-[88px]"
                        value={form.description || ""}
                        onChange={(e) => setForm({ ...form, description: e.target.value })} />
            </Field>
            <Field label="datetime (ISO)">
              <input className="input" value={form.datetime || ""}
                     onChange={(e) => setForm({ ...form, datetime: e.target.value })} />
            </Field>
            <label className="flex items-center gap-2 text-sm text-ink-200">
              <input
                type="checkbox" className="h-4 w-4 rounded border-ink-700 bg-ink-900"
                checked={!!form.is_planned}
                onChange={(e) => setForm({ ...form, is_planned: e.target.checked })}
              />
              planned event
            </label>
            <button className="btn-primary w-full justify-center" onClick={run} disabled={loading}>
              {loading ? "Predicting…" : "Run prediction"}
            </button>
          </div>
        </section>

        <section data-tour="predict-band" className="lg:col-span-3 space-y-4">
          {!result && !loading && (
            <div className="card p-6 text-center text-sm text-ink-400">
              Press <span className="pill-accent mx-1">Run prediction</span>
              to score the event.
            </div>
          )}
          {loading && <Loading label="scoring event…" />}
          {result && <ResultPanel result={result} />}
        </section>
      </div>
    </div>
  );
}

function Field({ label, children }) {
  return (
    <label className="block">
      <div className="label mb-0.5">{label}</div>
      {children}
    </label>
  );
}

function ResultPanel({ result }) {
  const c = result;
  return (
    <div className="space-y-4">
      <div className="card p-4">
        <div className="mb-3 flex items-start justify-between gap-2">
          <div>
            <div className="text-[11px] uppercase tracking-wider text-ink-400">clearance band</div>
            <div className="mt-0.5 text-sm text-ink-300">
              {c.corridor} · {c.event_subtype ? c.event_subtype : "incident"}
            </div>
          </div>
          <div className="flex flex-col items-end gap-1">
            <ConfidenceChip confidence={c.confidence} />
            <TierChip tier={c.closure_tier} />
          </div>
        </div>
        <Band p10={c.p10} p50={c.p50} p90={c.p90} />
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <MetricCard label="P50 clearance"  value={`${c.p50}m`}  sub="median" />
        <MetricCard label="Closure prob"   value={`${(c.closure_prob * 100).toFixed(0)}%`}
                    sub="blended ML + cause lookup" accent />
        <MetricCard label="Corridor risk" value={c.corridor_risk.toFixed(2)}
                    sub="0..1 historical" />
        <MetricCard label="Survival median" value={`${Math.round(c.survival_median_min)}m`}
                    sub="CoxPH (uses censored rows)" />
      </div>

      <div className="card p-4">
        <h3 className="mb-2 text-sm font-semibold text-ink-100">NLP cues parsed from the note</h3>
        <CueGrid cues={c.nlp_cues} />
        {c.nlp_cues.kannada_cues && (
          <p className="mt-2 text-[11px] text-accent-400">
            ✓ Kannada script detected and parsed (spec 02 §2b)
          </p>
        )}
      </div>

      {c.cascade_downstream && c.cascade_downstream.length > 0 && (
        <CascadeAlert edges={c.cascade_downstream} />
      )}

      <div className="card p-4">
        <h3 className="mb-2 text-sm font-semibold text-ink-100">because</h3>
        <ul className="space-y-1 text-sm text-ink-200">
          {c.because.map((b, i) => (
            <li key={i} className="flex items-start gap-2">
              <span className="mt-1.5 h-1.5 w-1.5 flex-none rounded-full bg-accent-500" />
              <span>{b}</span>
            </li>
          ))}
        </ul>
      </div>

      {c.prediction_id && (
        <div className="text-right text-[10px] text-ink-500 font-mono">
          prediction_id: {c.prediction_id}
        </div>
      )}
    </div>
  );
}

function CueGrid({ cues }) {
  const items = [
    ["lanes_blocked",   "Lanes blocked", "pill-bad"],
    ["needs_crane_tow", "Needs crane / tow", "pill-warn"],
    ["weather_water",   "Water / rain", "pill-accent"],
    ["agency_mention",  "Agency named", "pill-accent"],
    ["kannada_cues",     "Kannada script", "pill-good"],
  ];
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
      {items.map(([k, label, klass]) => (
        <div key={k} className="card-tight flex items-center justify-between px-3 py-2">
          <span className="text-sm text-ink-200">{label}</span>
          <span className={klass + " text-[10px]"}>
            {cues[k] ? "on" : "off"}
          </span>
        </div>
      ))}
    </div>
  );
}

function CascadeAlert({ edges }) {
  return (
    <div className="card border-warn-500/30 bg-warn-500/5 p-4">
      <div className="mb-2 flex items-center gap-2">
        <span className="pill-warn">⚠ cascade pre-alert</span>
        <span className="text-sm text-ink-200">
          This event may trigger downstream surges
        </span>
      </div>
      <div className="space-y-1 text-sm">
        {edges.slice(0, 4).map((e, i) => (
          <div key={i} className="flex items-center gap-2 text-ink-200">
            <span>⚠</span>
            <span>
              watch <span className="text-ink-50">{e.corridor}</span> in
              <span className="num text-warn-500 mx-1">{e.lag_min}m</span>
              <span className="text-ink-400">(r={e.r.toFixed(2)})</span>
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
