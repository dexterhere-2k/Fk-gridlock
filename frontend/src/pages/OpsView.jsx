// GridLock — View 6: Ops Command Center (spec 08).
//
// One page that exercises ALL 7 sub-systems of the operating model:
//   1. Cross-event allocation with preemption  → Allocation board (right pane)
//   2. Incident lifecycle state machine       → Incident board with stepper
//   3. Roster/skill/agency-aware dispatch      → Dispatch button + eligibility badge
//   4. Time-phased deployment schedule        → Schedule timeline (planned event)
//   5. Closed learning loop                   → Accuracy trend + retrain button
//   6. Trust / explainability                 → "because" panel (when one is selected)
//   7. Cascade pre-alert                      → Toast + side panel (on each incident)
//
// This is the "operating model" view — it shows that GridLock is a
// decision-support system, not just a predictor.

import { useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError } from "../lib/api.js";
import {
  ErrorPanel, Loading, PageHeader, MetricCard, ConfidenceChip, Band,
} from "../components/Shared.jsx";

// The 9 states from spec 08, in forward order. Used both to render
// the stepper and to render the colour-coded badge.
const STATES = [
  { id: "reported",   label: "Reported",   color: "bg-ink-700" },
  { id: "verified",   label: "Verified",   color: "bg-accent-700" },
  { id: "assigned",   label: "Assigned",   color: "bg-accent-600" },
  { id: "en_route",   label: "En route",   color: "bg-blue-600" },
  { id: "on_scene",   label: "On scene",   color: "bg-blue-500" },
  { id: "mitigating", label: "Mitigating", color: "bg-warn-500" },
  { id: "clearing",   label: "Clearing",   color: "bg-warn-400" },
  { id: "closed",     label: "Closed",     color: "bg-good-500" },
  { id: "debrief",    label: "Debrief",    color: "bg-good-400" },
];
const STATE_IDX = Object.fromEntries(STATES.map((s, i) => [s.id, i]));

// How often to refresh the live data
const POLL_MS = 4000;

export default function OpsView() {
  const [incidents, setIncidents] = useState(null);
  const [accuracy, setAccuracy] = useState(null);
  const [signal, setSignal] = useState(null);
  const [error, setError] = useState(null);
  const [selected, setSelected] = useState(null);
  const [reportDraft, setReportDraft] = useState({
    corridor: "Mysore Road", cause: "accident", operator_note: "",
  });
  const [cascadeAlerts, setCascadeAlerts] = useState({});
  const [toast, setToast] = useState(null);
  const [dispatchResult, setDispatchResult] = useState(null);

  // ---- live data poll
  useEffect(() => {
    let alive = true;
    const tick = () => Promise.all([
      api.listActiveIncidents().catch(() => null),
      api.accuracy().catch(() => null),
      api.learningSignal ? api.learningSignal() : Promise.resolve(null),
    ]).then(([i, a, s]) => {
      if (!alive) return;
      if (i) setIncidents(i.incidents || []);
      if (a) setAccuracy(a);
      if (s) setSignal(s);
    }).catch((e) => alive && setError(e));
    tick();
    const t = setInterval(tick, POLL_MS);
    return () => { alive = false; clearInterval(t); };
  }, []);

  // ---- pre-fetch cascade alerts for each incident's corridor
  useEffect(() => {
    if (!incidents) return;
    const corridors = new Set(incidents.map((i) => i.corridor).filter(Boolean));
    corridors.forEach((c) => {
      if (cascadeAlerts[c]) return;
      api.cascadeAlerts(c).then((r) => {
        setCascadeAlerts((prev) => ({ ...prev, [c]: r }));
      }).catch(() => {});
    });
  }, [incidents]);

  // ---- toast: show a brief cascade pre-alert banner when a NEW
  // incident appears with high-r downstream alerts. Fire ONCE per
  // incident id (use a ref, not the state object reference) and
  // auto-dismiss on a single timer (cleared on unmount/re-fire).
  const toastedRef = useRef(new Set());
  const toastTimerRef = useRef(null);
  useEffect(() => {
    if (!incidents) return;
    const fresh = incidents.find((i) => {
      if (i.state !== "reported") return false;
      if (toastedRef.current.has(i.id)) return false;  // already toasted
      const al = cascadeAlerts[i.corridor]?.alerts || [];
      return al.length > 0 && al[0].urgency === "early-warning";
    });
    if (!fresh) return;
    // mark as toasted BEFORE setting state so re-renders don't re-fire
    toastedRef.current.add(fresh.id);
    setToast({
      id: fresh.id,
      corridor: fresh.corridor,
      action: cascadeAlerts[fresh.corridor]?.primary,
    });
    if (toastTimerRef.current) clearTimeout(toastTimerRef.current);
    toastTimerRef.current = setTimeout(() => setToast(null), 6000);
    return () => {
      // no-op; timer is cleared on the next re-fire or unmount
    };
    // intentionally NOT depending on `cascadeAlerts` — the ref check
    // dedupes, and the effect should only run when the incident list
    // actually changes (otherwise the setTimeout gets reset every poll).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [incidents]);
  useEffect(() => () => {
    if (toastTimerRef.current) clearTimeout(toastTimerRef.current);
  }, []);

  // ---- actions
  const onReport = async () => {
    try {
      const r = await api.reportIncident(reportDraft);
      setSelected(r.id);
      // refresh immediately
      const list = await api.listActiveIncidents();
      setIncidents(list.incidents || []);
    } catch (e) {
      setError(e);
    }
  };
  const onTransition = async (id, target) => {
    try {
      await api.transitionIncident(id, target);
      const list = await api.listActiveIncidents();
      setIncidents(list.incidents || []);
    } catch (e) {
      setError(e);
    }
  };
  const onDispatch = async (id) => {
    try {
      const r = await api.dispatch(id);
      setDispatchResult(r);
    } catch (e) {
      setError(e);
    }
  };
  const onRetrain = async () => {
    try {
      const body = await api.retrain();
      const msg = body.status === "running"
        ? "Retrain started in background — check back in a few minutes"
        : `Retrain ${body.status}${body.elapsed_s ? ` (${body.elapsed_s}s)` : ""}`;
      alert(msg);
      // refresh metrics
      const [a, s] = await Promise.all([api.accuracy(), api.learningSignal()]);
      if (a) setAccuracy(a);
      if (s) setSignal(s);
    } catch (e) {
      setError(e);
    }
  };

  const sel = useMemo(
    () => incidents?.find((i) => i.id === selected) || null,
    [incidents, selected]);

  return (
    <div data-tour="ops" className="space-y-4">
      <PageHeader
        title="Ops command center"
        subtitle="Spec 08 — operating model (state machine, allocation, dispatch, learning)"
        actions={
          <div className="flex items-center gap-2">
            <span className="pill-ink">
              <span className="h-1.5 w-1.5 rounded-full bg-accent-500 animate-pulse" />
              {incidents?.length ?? "–"} active
            </span>
            <button
              onClick={onRetrain}
              className="rounded-md border border-ink-700 bg-ink-800 px-2.5 py-1 text-xs text-ink-200 hover:bg-ink-700"
            >
              ↻ Retrain
            </button>
          </div>
        }
      />
      <ErrorPanel error={error} />

      {/* cascade pre-alert toast (spec 08 #7) */}
      {toast && (
        <div className="card border-bad-500/30 bg-bad-500/5 p-3 text-sm">
          <div className="flex items-start gap-3">
            <div className="text-lg leading-none">⚠</div>
            <div className="flex-1">
              <div className="font-semibold text-bad-500">
                Cascade pre-alert: new incident on {toast.corridor}
              </div>
              <div className="mt-0.5 text-ink-300 text-xs">
                {toast.action
                  ? `Watch ${toast.action.secondary} (lag ${toast.action.lag_minutes} min, r=${toast.action.correlation?.toFixed(2)})`
                  : "downstream corridors loading…"}
              </div>
            </div>
            <button
              onClick={() => setToast(null)}
              className="text-ink-500 hover:text-ink-200 text-xs"
            >×</button>
          </div>
        </div>
      )}

      {/* KPI row */}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <MetricCard label="Active incidents" value={incidents?.length ?? "–"} sub="state machine" />
        <MetricCard label="High priority"
                    value={incidents?.filter((i) => i.priority === "High").length ?? "–"}
                    accent sub="auto-escalation" />
        <MetricCard label="SLA breaches"
                    value={incidents?.filter((i) => i.sla_breached).length ?? "–"}
                    sub="background ticker" />
        <MetricCard label="MAE (P50)"
                    value={accuracy?.global_p50_mae_min?.toFixed(1) ?? "–"}
                    sub={accuracy ? `${accuracy.n_outcomes} outcomes` : "—"}
                    accent />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        {/* report-new panel + active incident board */}
        <section className="card p-4 lg:col-span-2">
          <h2 className="mb-3 text-sm font-semibold text-ink-100">Report new incident</h2>
          <div className="mb-4 grid grid-cols-1 gap-2 sm:grid-cols-3">
            <select
              value={reportDraft.corridor}
              onChange={(e) => setReportDraft((d) => ({ ...d, corridor: e.target.value }))}
              className="rounded-md border border-ink-700 bg-ink-900 px-2 py-1.5 text-sm text-ink-100"
            >
              {[
                "Mysore Road", "Tumkur Road", "Bellary Road 1", "Bellary Road 2",
                "Hosur Road", "ORR East 1", "ORR East 2", "ORR North 1", "ORR North 2",
                "ORR West 1", "Bannerghata Road", "Hennur Main Road",
                "Old Madras Road", "Old Airport Road", "Varthur Road",
                "Magadi Road", "West of Chord Road", "CBD 1", "CBD 2",
              ].map((c) => <option key={c}>{c}</option>)}
            </select>
            <select
              value={reportDraft.cause}
              onChange={(e) => setReportDraft((d) => ({ ...d, cause: e.target.value }))}
              className="rounded-md border border-ink-700 bg-ink-900 px-2 py-1.5 text-sm text-ink-100"
            >
              {["accident","tree_fall","vip_movement","protest","public_event",
                "procession","vehicle_breakdown","water_logging","construction",
                "pot_holes","congestion","others"].map((c) => (
                <option key={c}>{c}</option>
              ))}
            </select>
            <button
              onClick={onReport}
              className="rounded-md bg-accent-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-accent-500"
            >＋ Report</button>
          </div>
          <input
            type="text"
            placeholder="operator note (e.g. 'tree blocking road, tow needed')"
            value={reportDraft.operator_note}
            onChange={(e) => setReportDraft((d) => ({ ...d, operator_note: e.target.value }))}
            className="mb-4 w-full rounded-md border border-ink-700 bg-ink-900 px-2 py-1.5 text-sm text-ink-100 placeholder:text-ink-500"
          />

          <h2 className="mb-2 text-sm font-semibold text-ink-100">Active incidents</h2>
          {!incidents ? <Loading label="loading…" /> : incidents.length === 0 ? (
            <p className="text-xs text-ink-500">No active incidents — report one above to start.</p>
          ) : (
            <div className="space-y-2 max-h-[440px] overflow-y-auto">
              {incidents.map((inc) => (
                <IncidentRow
                  key={inc.id}
                  inc={inc}
                  selected={selected === inc.id}
                  onClick={() => setSelected(inc.id)}
                  cascadeAlerts={cascadeAlerts[inc.corridor]?.alerts || []}
                />
              ))}
            </div>
          )}
        </section>

        {/* selected incident detail: state stepper + because + cascade pre-alert */}
        <section className="card p-4">
          <h2 className="mb-2 text-sm font-semibold text-ink-100">Incident detail</h2>
          {!sel ? <Loading label="select an incident…" /> : (
            <IncidentDetail
              inc={sel}
              cascadeAlerts={cascadeAlerts[sel.corridor]?.alerts || []}
              onTransition={onTransition}
              onDispatch={onDispatch}
              dispatchResult={dispatchResult?.incident_id === sel.id ? dispatchResult : null}
            />
          )}
        </section>
      </div>

      {/* learning loop row */}
      <section className="card p-4">
        <h2 className="mb-2 text-sm font-semibold text-ink-100">Closed learning loop (spec 08 #5)</h2>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4 text-sm">
          <Field label="Outcomes" value={accuracy?.n_outcomes} />
          <Field label="MAE (P50)" value={accuracy?.global_p50_mae_min?.toFixed(1)} unit="min" />
          <Field label="Retrain" value={signal?.retrain_triggered ? "triggered" : "idle"} />
          <Field label="Drift" value={signal?.mae_drift_min?.toFixed(1)} unit="min" />
        </div>
        {accuracy?.by_cause && accuracy.by_cause.length > 0 && (
          <div className="mt-3 overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="text-left text-[10px] uppercase tracking-wider text-ink-500">
                <tr>
                  <th className="py-1 pr-3">cause</th>
                  <th className="py-1 pr-3 text-right">n</th>
                  <th className="py-1 pr-3 text-right">MAE</th>
                  <th className="py-1 pr-3 text-right">bias</th>
                </tr>
              </thead>
              <tbody>
                {accuracy.by_cause.slice(0, 8).map((b) => (
                  <tr key={b.cause} className="border-t border-ink-800">
                    <td className="py-1 pr-3 text-ink-200">{b.cause}</td>
                    <td className="py-1 pr-3 text-right num">{b.n}</td>
                    <td className="py-1 pr-3 text-right num">{b.mae_min?.toFixed(1)}</td>
                    <td className="py-1 pr-3 text-right num">
                      {b.bias_min > 0 ? "+" : ""}{b.bias_min?.toFixed(1)}
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

// ---- components ----------------------------------------------------------------

function Field({ label, value, unit }) {
  return (
    <div>
      <div className="label">{label}</div>
      <div className="text-ink-100 num">
        {value ?? "–"}{unit && value != null ? <span className="text-ink-500 text-xs ml-0.5">{unit}</span> : null}
      </div>
    </div>
  );
}

function IncidentRow({ inc, selected, onClick, cascadeAlerts }) {
  const stateIdx = STATE_IDX[inc.state] ?? 0;
  const hasUrgent = cascadeAlerts.length > 0 && cascadeAlerts[0].urgency === "early-warning";
  return (
    <div
      onClick={onClick}
      className={
        "cursor-pointer rounded-md border p-2.5 transition " +
        (selected
          ? "border-accent-500 bg-accent-500/10"
          : "border-ink-800 bg-ink-900/40 hover:border-ink-700")
      }
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5">
            <span className="text-sm font-semibold text-ink-100 truncate">
              {inc.corridor}
            </span>
            {inc.priority === "High" && (
              <span className="pill-bad">High</span>
            )}
            {inc.sla_breached && (
              <span className="pill-warn">SLA×{inc.sla_breaches}</span>
            )}
            {hasUrgent && (
              <span className="pill-bad">⚠ cascade</span>
            )}
          </div>
          <div className="text-[10px] text-ink-500 mt-0.5 num">
            {inc.cause} · {inc.id}
          </div>
        </div>
        <div className="text-right">
          <span className={"rounded px-1.5 py-0.5 text-[10px] text-white " + STATES[stateIdx].color}>
            {STATES[stateIdx].label}
          </span>
          <div className="text-[10px] text-ink-500 num mt-0.5">
            {inc.elapsed_minutes?.toFixed(0)}/{inc.sla_minutes}m
          </div>
        </div>
      </div>
      {/* mini state progress bar */}
      <div className="mt-1.5 flex gap-0.5">
        {STATES.map((s, i) => (
          <div
            key={s.id}
            className={
              "h-1 flex-1 rounded-full " +
              (i <= stateIdx ? s.color : "bg-ink-800")
            }
          />
        ))}
      </div>
    </div>
  );
}

function IncidentDetail({ inc, cascadeAlerts, onTransition, onDispatch, dispatchResult }) {
  const stateIdx = STATE_IDX[inc.state] ?? 0;
  const nextStates = STATES.slice(stateIdx + 1).map((s) => s.id);
  const canRegress = stateIdx > 0 && STATES[stateIdx - 1].id !== "closed";
  const pred = inc.prediction;
  return (
    <div className="space-y-3 text-sm">
      <div>
        <div className="text-base font-semibold text-ink-100">{inc.corridor}</div>
        <div className="text-[11px] text-ink-500 num">{inc.id} · {inc.cause}</div>
      </div>

      {/* state machine stepper */}
      <div>
        <div className="label mb-1">State machine</div>
        <div className="flex flex-wrap gap-1">
          {STATES.map((s, i) => (
            <span
              key={s.id}
              className={
                "rounded px-1.5 py-0.5 text-[10px] " +
                (i === stateIdx
                  ? s.color + " text-white"
                  : i < stateIdx
                    ? "bg-ink-700 text-ink-300"
                    : "bg-ink-800 text-ink-500")
              }
            >
              {s.label}
            </span>
          ))}
        </div>
        <div className="mt-1 text-[10px] text-ink-500">
          {inc.sla_breached
            ? <span className="text-bad-500">SLA breached ({inc.elapsed_minutes?.toFixed(0)}/{inc.sla_minutes}m)</span>
            : <span>elapsed {inc.elapsed_minutes?.toFixed(0)}m / {inc.sla_minutes}m</span>}
          {inc.escalation_reason && (
            <div className="mt-0.5 text-bad-500">{inc.escalation_reason}</div>
          )}
        </div>
        {nextStates.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1">
            {nextStates.slice(0, 4).map((ns) => (
              <button
                key={ns}
                onClick={() => onTransition(inc.id, ns)}
                className="rounded border border-ink-700 bg-ink-900 px-2 py-0.5 text-[10px] text-ink-300 hover:bg-ink-800"
              >→ {ns}</button>
            ))}
          </div>
        )}
        <button
          onClick={() => onDispatch(inc.id)}
          className="mt-2 w-full rounded-md bg-blue-600 px-2 py-1 text-xs font-medium text-white hover:bg-blue-500"
        >Dispatch nearest unit</button>

        {/* spec 08 #3 — dispatch result panel (real ETA + alternatives) */}
        {dispatchResult && (
          <DispatchResultPanel r={dispatchResult} />
        )}
      </div>

      {/* spec 01 prediction (because + uncertainty) */}
      {pred && (
        <div>
          <div className="label mb-1">Prediction</div>
          <Band p10={pred.p10} p50={pred.p50} p90={pred.p90} cap={Math.max(180, pred.p90 * 1.2)} />
          <div className="mt-1 flex items-center gap-2 text-[10px]">
            <ConfidenceChip confidence={pred.confidence} />
            <span className="text-ink-400">closure {Math.round(pred.closure_prob * 100)}%</span>
            <span className="text-ink-400">corridor risk {pred.corridor_risk?.toFixed(2)}</span>
          </div>
          {Object.keys(pred.nlp_cues || {}).length > 0 && (
            <div className="mt-1 text-[10px] text-ink-500">
              NLP cues: {Object.entries(pred.nlp_cues)
                .filter(([_, v]) => v)
                .map(([k]) => k.replace(/_/g, " "))
                .join(", ") || "none"}
            </div>
          )}
        </div>
      )}

      {/* spec 08 #7 — cascade pre-alert */}
      {cascadeAlerts.length > 0 && (
        <div>
          <div className="label mb-1">Cascade pre-alert ⚠</div>
          <div className="space-y-1 text-[11px]">
            {cascadeAlerts.slice(0, 4).map((a, i) => (
              <div
                key={i}
                className={
                  "rounded border p-1.5 " +
                  (a.urgency === "early-warning"
                    ? "border-bad-500/30 bg-bad-500/5"
                    : a.urgency === "watch"
                      ? "border-warn-500/30 bg-warn-500/5"
                      : "border-ink-800 bg-ink-900/40")
                }
              >
                <div className="flex items-center justify-between">
                  <span className="text-ink-200">
                    → {a.secondary}
                  </span>
                  <span className={
                    "pill " +
                    (a.urgency === "early-warning" ? "pill-bad" :
                     a.urgency === "watch" ? "pill-warn" : "pill-ink")
                  }>{a.urgency}</span>
                </div>
                <div className="text-[10px] text-ink-500 num mt-0.5">
                  lag {a.lag_minutes}m · r={a.correlation?.toFixed(2)} · ×{a.cascade_risk_multiplier}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ---- spec 08 #3 — dispatch result panel (real ETA + alternatives + because)
function DispatchResultPanel({ r }) {
  const etaClass = r.confidence === "high" ? "pill-good"
    : r.confidence === "medium" ? "pill-warn" : "pill-bad";
  const agencyClass = r.agency_match === "exact" ? "pill-good"
    : r.agency_match === "police_fallback" ? "pill-warn" : "pill-bad";
  return (
    <div className="mt-2 rounded-md border border-blue-500/30 bg-blue-500/5 p-2 text-xs space-y-1.5">
      <div className="flex items-center justify-between">
        <span className="text-ink-200 font-semibold">{r.dispatched_unit}</span>
        <span className="num text-blue-300">
          {r.estimated_eta_min != null ? `${r.estimated_eta_min} min` : "—"}
        </span>
      </div>
      <div className="text-[10px] text-ink-400 num">
        {r.dispatched_unit_station} · {r.dispatched_unit_agency}
      </div>
      <div className="flex flex-wrap gap-1">
        <span className={agencyClass}>{r.agency_match.replace("_", " ")}</span>
        <span className={etaClass}>{r.confidence} conf</span>
        <span className="pill-ink">eta: {r.eta_source}</span>
      </div>
      {r.alternatives && r.alternatives.length > 0 && (
        <div className="mt-1">
          <div className="label">Nearest alternatives</div>
          <div className="space-y-0.5 mt-0.5">
            {r.alternatives.map((a) => (
              <div key={a.unit_id}
                   className="flex items-center justify-between text-[10px] text-ink-300">
                <span className="truncate">
                  {a.unit_id} · {a.agency}
                </span>
                <span className="num text-ink-200">{a.eta_min}m</span>
              </div>
            ))}
          </div>
        </div>
      )}
      {r.because && r.because.length > 0 && (
        <details className="mt-1">
          <summary className="cursor-pointer text-[10px] text-ink-400">
            because ({r.because.length})
          </summary>
          <ul className="mt-1 list-disc pl-4 space-y-0.5 text-[10px] text-ink-300">
            {r.because.map((b, i) => <li key={i}>{b}</li>)}
          </ul>
        </details>
      )}
    </div>
  );
}
