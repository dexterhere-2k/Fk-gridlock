// View 5 — Debrief: plan vs actual + accuracy trend (the learning loop).
// Hits /api/accuracy for the live accuracy readout and /api/debrief/{id}
// for a per-incident deep dive.

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

  const refresh = async () => {
    try {
      const [acc, rc, ms] = await Promise.all([
        api.accuracy(), api.riskCorridors(), api.mapStations(),
      ]);
      setAccuracy(acc);
      setOutcomes(acc.points || []);
      setCorridors(rc.corridors || []);
      setStations((ms.geojson?.features || []).map((f) => f.properties));
    } catch (e) { setError(e); }
  };

  useEffect(() => { refresh(); }, []);

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
    <div data-tour="debrief" className="space-y-4">
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
                   placeholder="INC-1" />
          </label>
          <button className="btn-primary" onClick={submit} disabled={busy || !eventId}>
            {busy ? "Looking up…" : "Get debrief"}
          </button>
          <button className="btn-secondary" onClick={seedOutcome} disabled={busy || !eventId}>
            + log sample outcome
          </button>
        </div>
        {debrief && <DebriefPanel debrief={debrief} debriefPin={debriefPin}
                              corridors={corridors} stations={stations} />}
      </section>

      {outcomes.length > 0 && <OutcomesTable outcomes={outcomes} />}
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
