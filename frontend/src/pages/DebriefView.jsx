// View 5 — Debrief: plan vs actual + accuracy trend (the learning loop).
// Hits /api/accuracy for live accuracy, /api/debrief/{id} for per-incident
// deep dive, /api/learning/signal for per-cause drift, and
// /api/learning/retrain to trigger model retraining.

import { useEffect, useState } from "react";
import { api } from "../lib/api.js";
import {
  ErrorPanel, Loading, MetricCard, PageHeader,
} from "../components/Shared.jsx";
import MapplsMap from "../components/MapplsMap.jsx";

export default function DebriefView() {
  const [accuracy, setAccuracy] = useState(null);
  const [error, setError] = useState(null);
  const [eventId, setEventId] = useState("");
  const [debrief, setDebrief] = useState(null);
  const [outcomes, setOutcomes] = useState([]);
  const [corridors, setCorridors] = useState([]);
  const [stations, setStations] = useState([]);
  const [debriefPin, setDebriefPin] = useState(null);
  const [busy, setBusy] = useState(false);
  const [signal, setSignal] = useState(null);
  const [retraining, setRetraining] = useState(false);

  const refresh = async () => {
    try {
      const [acc, rc, ms, sig] = await Promise.all([
        api.accuracy(), api.riskCorridors(), api.mapStations(),
        api.learningSignal().catch(() => null),
      ]);
      setAccuracy(acc);
      setOutcomes(acc.points || []);
      setCorridors(rc.corridors || []);
      setStations((ms.geojson?.features || []).map((f) => f.properties));
      if (sig) {
        // Response shape: { signal: {...}, retrain_triggered, trigger_reason, last_run }
        setSignal({
          ...(sig.signal || sig),
          retrain_triggered: sig.retrain_triggered,
          trigger_reason: sig.trigger_reason,
          last_run: sig.last_run,
        });
      }
    } catch (e) { setError(e); }
  };

  useEffect(() => { refresh(); const t = setInterval(refresh, 15000); return () => clearInterval(t); }, []);

  const handleRetrain = async () => {
    setRetraining(true);
    try { await api.retrain(); await refresh(); }
    catch (e) { setError(e); }
    finally { setRetraining(false); }
  };

  const submit = async () => {
    if (!eventId) return;
    setBusy(true); setError(null); setDebrief(null); setDebriefPin(null);
    try {
      const d = await api.debrief(eventId);
      setDebrief(d);
      // spec 07 — pull a single incident pin from the historical data
      // (if the event_id matches a real row in the clean parquet).
      try {
        const mi = await api.mapIncidents(2000);
        const feats = mi.geojson?.features || [];
        const match = feats.find((f) => f.properties.id === eventId);
        if (match) setDebriefPin(match.properties);
      } catch { /* pin is best-effort */ }
    } catch (e) { setError(e); }
    finally { setBusy(false); }
  };

  const seedOutcome = async () => {
    if (!eventId) return;
    setBusy(true);
    try {
      await api.recordOutcome({
        event_id: eventId,
        actual_p50_min: 75 + Math.random() * 30,
        actual_p90_min: 180 + Math.random() * 40,
        actual_closure: Math.random() < 0.4,
        actual_officers_deployed: 8,
        actual_barricades: 3,
        notes: "seeded from /debrief demo",
      });
      await refresh();
    } catch (e) { setError(e); }
    finally { setBusy(false); }
  };

  return (
    <div data-tour="debrief" className="flex h-full min-h-0 flex-col gap-2 overflow-y-auto p-3">
      <PageHeader
        title="Debrief & learning loop"
        subtitle="Plan-vs-actual variance + accuracy trend over the prediction ledger"
      />
      <ErrorPanel error={error} />

      {accuracy ? (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <MetricCard label="Outcomes logged" value={accuracy.n_outcomes}
                      sub="learning-loop samples" />
          <MetricCard label="P50 MAE" value={`${accuracy.p50_mae_min}m`}
                      sub="actual vs predicted median" accent />
          <MetricCard label="Closure accuracy"
                      value={`${(accuracy.closure_accuracy * 100).toFixed(0)}%`}
                      sub="predicted closure vs actual" />
          <MetricCard label="API status" value="ok" sub="ledger up to date" />
        </div>
      ) : <Loading label="loading accuracy…" />}

      <section className="card p-4">
        <h2 className="mb-2 text-sm font-semibold text-ink-100">Look up a debrief</h2>
        <div className="flex flex-wrap items-end gap-2">
          <label className="block">
            <div className="label mb-0.5">event_id</div>
            <input className="input w-72" value={eventId}
                   onChange={(e) => setEventId(e.target.value)}
                   placeholder="pick from samples below" />
          </label>
          <button className="btn-primary" onClick={submit} disabled={busy || !eventId}>
            {busy ? "Looking up…" : "Get debrief"}
          </button>
          <button className="btn-secondary" onClick={seedOutcome} disabled={busy || !eventId}>
            + log sample outcome
          </button>
          {outcomes.length > 0 && (
            <div className="flex flex-wrap gap-1.5 text-[11px] text-ink-400">
              <span>recent:</span>
              {outcomes.slice(0, 5).map((o) => (
                <button key={o.event_id} onClick={() => setEventId(o.event_id)}
                        className="rounded border border-ink-700 bg-ink-900 px-2 py-0.5 text-ink-200 hover:bg-ink-800 transition cursor-pointer font-mono">{o.event_id.slice(0, 16)}…</button>
              ))}
            </div>
          )}
          {outcomes.length === 0 && (
            <p className="text-[11px] text-ink-500">No outcomes in the ledger yet. Log one with the button above, or score an event in Predict first.</p>
          )}
        </div>
        {debrief && <DebriefPanel debrief={debrief} debriefPin={debriefPin}
                              corridors={corridors} stations={stations} />}
      </section>

      {outcomes.length > 0 && <OutcomesTable outcomes={outcomes} />}

      {/* Learning loop: per-cause drift + retrain trigger */}
      {signal && (
        <section className="card p-4">
          <h2 className="mb-3 text-sm font-semibold text-ink-100">Learning loop</h2>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <MetricCard label="Outcomes logged" value={signal.n_outcomes}
                        sub="since last retrain" />
            <MetricCard label="Global P50 MAE"
                        value={signal.global_p50_mae_min != null ? `${signal.global_p50_mae_min}m` : "–"}
                        sub="predicted vs actual" accent />
            <MetricCard label="Closure accuracy"
                        value={signal.global_closure_accuracy != null
                               ? `${(signal.global_closure_accuracy * 100).toFixed(0)}%` : "–"}
                        sub="closure prediction" />
            <MetricCard label="Retrain trigger"
                        value={signal.retrain_triggered ? "YES" : "no"}
                        sub={signal.trigger_reason || "waiting for data"}
                        accent={signal.retrain_triggered} />
          </div>

          {signal.per_cause && Object.keys(signal.per_cause).length > 0 && (
            <div className="mt-3">
              <h3 className="mb-1 text-[11px] uppercase tracking-wider text-ink-400">Per-cause drift</h3>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="text-left text-[10px] uppercase tracking-wider text-ink-500">
                    <tr>
                      <th className="py-1 pr-3">cause</th>
                      <th className="py-1 pr-3 text-right">n</th>
                      <th className="py-1 pr-3 text-right">p50 mae</th>
                      <th className="py-1 pr-3 text-right">closure acc</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(signal.per_cause)
                      .sort((a, b) => b[1].n - a[1].n)
                      .slice(0, 8)
                      .map(([cause, stats]) => (
                        <tr key={cause} className="border-t border-ink-800">
                          <td className="py-1.5 pr-3 num text-ink-200">{cause}</td>
                          <td className="py-1.5 pr-3 text-right num text-ink-300">{stats.n}</td>
                          <td className="py-1.5 pr-3 text-right num">{stats.p50_mae_min}m</td>
                          <td className="py-1.5 pr-3 text-right num text-ink-300">
                            {(stats.closure_accuracy * 100).toFixed(0)}%
                          </td>
                        </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          <button
            className="btn-primary mt-3"
            onClick={handleRetrain}
            disabled={retraining || !signal.retrain_triggered}
            title={signal.trigger_reason || ""}
          >
            {retraining ? "Retraining…" : "Retrain model"}
          </button>
        </section>
      )}
    </div>
  );
}

function DebriefPanel({ debrief, debriefPin, corridors, stations }) {
  return (
    <div className="mt-4 space-y-3">
      {/* spec 07 — show the incident on the Bengaluru map */}
      {debriefPin && debriefPin.corridor && (
        <div>
          <h3 className="mb-1 text-[11px] uppercase tracking-wider text-ink-400">
            Incident location
          </h3>
          <MapplsMap
            corridors={corridors || []}
            stations={stations || []}
            incidents={[debriefPin]}
            highlight={debriefPin.id}
            width={720}
            height={280}
          />
        </div>
      )}
      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        <div className="card-tight p-3">
          <h3 className="label">Plan (predicted)</h3>
          <pre className="mt-1 whitespace-pre-wrap text-[11px] text-ink-200">
            {JSON.stringify(debrief.plan, null, 2).slice(0, 400)}
          </pre>
        </div>
        <div className="card-tight p-3">
          <h3 className="label">Actual</h3>
          <pre className="mt-1 whitespace-pre-wrap text-[11px] text-ink-200">
            {JSON.stringify(debrief.actual, null, 2).slice(0, 400)}
          </pre>
        </div>
        <div className="card-tight p-3">
          <h3 className="label">Variance</h3>
          <pre className="mt-1 whitespace-pre-wrap text-[11px] text-ink-200">
            {JSON.stringify(debrief.variance, null, 2)}
          </pre>
        </div>
      </div>
    </div>
  );
}

function OutcomesTable({ outcomes }) {
  return (
    <section className="card p-4">
      <h2 className="mb-2 text-sm font-semibold text-ink-100">Recent outcomes</h2>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-[10px] uppercase tracking-wider text-ink-500">
            <tr>
              <th className="py-1 pr-3">event</th>
              <th className="py-1 pr-3 text-right">pred p50</th>
              <th className="py-1 pr-3 text-right">actual p50</th>
              <th className="py-1 pr-3 text-right">err (m)</th>
              <th className="py-1 pr-3 text-right">pred closure</th>
              <th className="py-1 pr-3 text-right">actual closure</th>
            </tr>
          </thead>
          <tbody>
            {outcomes.slice(-20).reverse().map((o, i) => (
              <tr key={i} className="border-t border-ink-800">
                <td className="py-1.5 pr-3 num text-ink-300">{o.event_id}</td>
                <td className="py-1.5 pr-3 text-right num text-ink-300">{o.predicted_p50?.toFixed?.(0) ?? "–"}</td>
                <td className="py-1.5 pr-3 text-right num">{o.actual_p50?.toFixed?.(0) ?? "–"}</td>
                <td className={"py-1.5 pr-3 text-right num " + errClass(o.error_min)}>
                  {o.error_min?.toFixed?.(1) ?? "–"}
                </td>
                <td className="py-1.5 pr-3 text-right num">
                  {o.predicted_closure ? "✓" : "—"}
                </td>
                <td className="py-1.5 pr-3 text-right num">
                  {o.actual_closure ? "✓" : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function errClass(e) {
  if (e == null) return "text-ink-300";
  if (e < 20) return "text-good-500";
  if (e < 60) return "text-warn-500";
  return "text-bad-500";
}
