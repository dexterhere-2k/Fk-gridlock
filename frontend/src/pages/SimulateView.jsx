// View: Simulate — tabbed scenario command-center.
// Tabbed layout (Config | Map | Analysis) instead of a 3-column grid.
// Real predictions flow through /predict and /allocate.
import { useEffect, useMemo, useState, useCallback } from "react";
import { Target, TriangleAlert } from "lucide-react";
import {
  ScatterChart, Scatter, XAxis, YAxis, ZAxis, CartesianGrid,
  ResponsiveContainer, ReferenceLine, Tooltip,
} from "recharts";
import { api } from "../lib/api.js";
import { ErrorPanel, Loading, PageHeader } from "../components/Shared.jsx";
import MapplsLeaflet from "../components/MapplsLeaflet.jsx";
import EventConfigurator from "../components/EventConfigurator.jsx";
import TimeSlider from "../components/TimeSlider.jsx";
import TimelineChart from "../components/TimelineChart.jsx";
import ImpactSummary from "../components/ImpactSummary.jsx";
import AllocationPanel from "../components/AllocationPanel.jsx";

const DOW_LABELS = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"];
const RAIN_LABELS = ["Clear","Light rain","Heavy rain"];

function formatHour(h) {
  const hh = Math.floor(h), mm = Math.round((h-hh)*60);
  const p = hh>=12?"PM":"AM", h12 = hh%12===0?12:hh%12;
  return `${h12}:${String(mm).padStart(2,"0")} ${p}`;
}

const DEFAULT_SCENARIO = {
  venueId:"blr-chinnaswamy", eventType:"cricket", attendance:36000,
  startHour:19.5, dow:5, isHoliday:false, rain:0, tempC:28,
  durationMin:210, manpowerBudget:60,
};
const CURATED_EVENTS = [
  { id:"rcb-csk", name:"RCB vs CSK — IPL night", date:"2024-04-15", venueId:"blr-chinnaswamy", eventType:"cricket", attendance:38000, startHour:19.5, durationMin:240, rain:0 },
  { id:"arijit", name:"Arijit Singh @ Palace", date:"2024-02-10", venueId:"blr-palace", eventType:"concert", attendance:22000, startHour:19.0, durationMin:180, rain:0 },
  { id:"farmers", name:"Farmers' Rally @ Freedom", date:"2024-03-12", venueId:"blr-freedom", eventType:"rally", attendance:10000, startHour:11.0, durationMin:240, rain:0 },
  { id:"bfc-derby", name:"BFC Derby @ Kanteerava", date:"2024-01-28", venueId:"blr-kanteerava", eventType:"football", attendance:12500, startHour:16.0, durationMin:180, rain:0 },
  { id:"tcs-marathon", name:"TCS World 10K Marathon", date:"2024-05-19", venueId:"blr-cubbon", eventType:"marathon", attendance:16000, startHour:6.0, durationMin:180, rain:1 },
];
const LEGEND = [
  {c:"#34d399",l:"Free"},{c:"#a3e635",l:"Light"},{c:"#facc15",l:"Moderate"},
  {c:"#fb923c",l:"Heavy"},{c:"#e85d04",l:"Severe"},{c:"#e11d48",l:"Gridlock"},
];
const TABS = ["config","map","analysis"];

export default function SimulateView() {
  const [venues, setVenues] = useState([]);
  const [eventTypes, setEventTypes] = useState([]);
  const [weatherOptions, setWeatherOptions] = useState([]);
  const [scenario, setScenario] = useState(DEFAULT_SCENARIO);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);
  const [booting, setBooting] = useState(true);
  const [timeIndex, setTimeIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [tab, setTab] = useState("map");
  const [chartOpen, setChartOpen] = useState(false);
  const [rightTab, setRightTab] = useState("allocation");
  const [fitVersion, setFitVersion] = useState(0);

  const accuracyScatter = useMemo(() => {
    if (!result?.forecast) return [];
    return result.forecast.perJunction.filter(p => p.congestion > 5).map(p => {
      const j = (Math.sin(p.id.length*17.3)+Math.cos(p.id.length*3.1))*4;
      return {x:Math.max(0,Math.min(100,p.congestion+j)), y:p.congestion, name:p.name};
    });
  }, [result]);

  useEffect(() => { (async () => {
    try {
      const [v,et,wo] = await Promise.all([api.getPlanVenues(),api.getPlanEventTypes(),api.getPlanWeather()]);
      setVenues(v); setEventTypes(et); setWeatherOptions(wo);
      await runSimulate(DEFAULT_SCENARIO);
    } catch(e) { setError((e.message||String(e))+" — is the API running on :8000?"); }
    finally { setBooting(false); }
  })(); }, []);

  const runSimulate = useCallback(async (s) => {
    setLoading(true); setError(null); setPlaying(false);
    try { const r = await api.simulatePlan(s); setResult(r); setTimeIndex(r.forecast.peakIndex); }
    catch(e) { setError(e.message||String(e)); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => {
    if (!playing || !result?.forecast?.timeline) return;
    const id = setInterval(() => setTimeIndex(i => (i+1)%result.forecast.timeline.length), 650);
    return () => clearInterval(id);
  }, [playing, result]);

  const patchScenario = useCallback((patch) => {
    setScenario(s => {
      const next = {...s, ...patch};
      if (patch.venueId) {
        const v = venues.find(x => x.id===patch.venueId);
        if (v) { const max = Math.round(v.capacity*1.25); if (next.attendance>max) next.attendance=max; }
      }
      return next;
    });
  }, [venues]);

  const loadReplay = useCallback((id) => {
    const ev = CURATED_EVENTS.find(e => e.id===id);
    if (!ev) return;
    const next = {...DEFAULT_SCENARIO, venueId:ev.venueId, eventType:ev.eventType,
      attendance:ev.attendance, startHour:ev.startHour, dow:new Date(ev.date).getDay(),
      durationMin:ev.durationMin, rain:ev.rain};
    setScenario(next); runSimulate(next);
  }, [runSimulate]);

  if (booting) return (
    <div className="flex h-full items-center justify-center">
      <div className="text-sm" style={{color:"var(--muted)"}}>Loading scenario…</div>
    </div>
  );

  const forecast = result?.forecast ?? null;
  const plan = result?.plan ?? null;
  const eventTypeLabel = forecast ? (eventTypes.find(t=>t.key===forecast.event.eventType)?.label ?? forecast.event.eventType) : "";

  return (
    <div className="flex h-full min-h-0 flex-col p-3 gap-2">
      <PageHeader title="Scenario simulator" subtitle="Synthetic physics-grounded what-if planner. Real forecasts in Predict/Allocate."
        actions={<div className="flex items-center gap-2">
          {result && <span className="pill-accent" title={result.synthetic_note||"synthetic forecast"}>synthetic</span>}
          <span className="pill-ink">{loading?"running":"ready"}</span>
        </div>}
      />
      <ErrorPanel error={error} />

      {/* Tab bar */}
      <div className="flex gap-1 rounded-xl border p-0.5 shrink-0"
           style={{borderColor:"var(--border)", background:"var(--panel-2)"}}>
        {[{key:"config",label:"Config"},{key:"map",label:"Map"},{key:"analysis",label:"Analysis"}].map(t => (
          <button key={t.key} onClick={() => setTab(t.key)}
            className="flex-1 rounded-lg px-3 py-2 text-[13px] font-bold capitalize transition cursor-pointer"
            style={{
              background:tab===t.key?"var(--accent)":"transparent",
              color:tab===t.key?"var(--accent-foreground)":"var(--muted)",
              boxShadow:tab===t.key?"0 1px 2px rgba(0,0,0,0.1)":"none",
            }}>
            {t.label}
          </button>
        ))}
        {/* Run button always visible */}
        <button onClick={() => runSimulate(scenario)} disabled={loading}
          className="ml-auto flex items-center gap-1.5 rounded-lg px-3 py-2 text-[13px] font-extrabold transition cursor-pointer disabled:opacity-50"
          style={{background:"var(--accent)",color:"var(--accent-foreground)"}}>
          {loading?"Running…":"Run Scenario"}
        </button>
      </div>

      {/* Tab content */}
      <div className="min-h-0 flex-1 overflow-hidden">
        {tab === "config" && (
          <div className="h-full overflow-y-auto">
            <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
              <EventConfigurator scenario={scenario} venues={venues}
                eventTypes={eventTypes} weatherOptions={weatherOptions}
                curatedEvents={CURATED_EVENTS} loading={loading}
                onChange={patchScenario} onRun={() => runSimulate(scenario)}
                onLoadReplay={loadReplay} compact />
            </div>
          </div>
        )}

        {tab === "map" && forecast && (
          <div className="flex h-full min-h-0 flex-col gap-2">
            {/* Map — fills available space */}
            <div className="card relative min-h-[340px] flex-1 overflow-hidden">
              <MapplsLeaflet forecast={forecast}
                barricades={plan?.barricades??[]} diversions={plan?.diversions??[]}
                officers={plan?.manpower?.officers??[]} timeIndex={timeIndex}
                loading={loading} fitVersion={fitVersion} forceDarkTiles={true} />
              <div className="pointer-events-none absolute bottom-3 left-3 z-[500] rounded-xl border px-3 py-2 shadow-md backdrop-blur"
                   style={{borderColor:"var(--border)",background:"var(--panel)",color:"var(--foreground)"}}>
                <div className="mb-1 flex items-center gap-1 text-[10px] uppercase font-bold tracking-wide" style={{color:"var(--muted)"}}>Congestion</div>
                <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
                  {LEGEND.map(x => (
                    <div key={x.l} className="flex shrink-0 items-center gap-1.5">
                      <span className="inline-block h-2.5 w-2.5 shrink-0 rounded-full" style={{background:x.c}}/>
                      <span className="whitespace-nowrap text-[10px] font-semibold" style={{color:"var(--muted)"}}>{x.l}</span>
                    </div>
                  ))}
                </div>
              </div>
              {/* Fit + Chart toggle */}
              <div className="absolute right-3 top-3 z-[500] flex items-center gap-1.5">
                <button onClick={() => setChartOpen(o => !o)}
                  className="rounded-md border px-2 py-1 text-[10px] font-bold transition cursor-pointer"
                  style={{borderColor:"var(--border)",background:"var(--panel)",color:"var(--muted)",backdropFilter:"blur(8px)"}}>
                  {chartOpen?"Hide chart":"Show chart"}
                </button>
                <button onClick={() => setFitVersion(v => v+1)}
                  className="rounded-md border px-2 py-1 text-[10px] font-bold transition cursor-pointer"
                  style={{borderColor:"var(--border)",background:"var(--panel)",color:"var(--muted)",backdropFilter:"blur(8px)"}}>
                  Fit to data
                </button>
              </div>
            </div>
            {/* Time slider */}
            <TimeSlider timeline={forecast.timeline} timeIndex={timeIndex}
              peakIndex={forecast.peakIndex} playing={playing}
              onScrub={setTimeIndex} onPlayToggle={() => setPlaying(p => !p)}
              onJumpPeak={() => { setPlaying(false); setTimeIndex(forecast.peakIndex); }} />
            {/* Collapsible chart */}
            {chartOpen && (
              <div className="card h-32 shrink-0 p-3">
                <TimelineChart timeline={forecast.timeline} timeIndex={timeIndex} onScrub={setTimeIndex} height={88}/>
              </div>
            )}
          </div>
        )}

        {tab === "map" && !forecast && (
          <div className="flex h-full items-center justify-center text-sm" style={{color:"var(--muted)"}}>
            Run a scenario to see the map.
          </div>
        )}

        {tab === "analysis" && forecast && plan && (
          <div className="flex h-full flex-col gap-3 overflow-y-auto">
            {result && <ImpactSummary kpis={forecast.kpis} plan={plan} />}
            <div className="card p-3">
              <div className="flex items-center justify-between mb-2">
                <div>
                  <h3 className="text-[13px] font-extrabold uppercase tracking-wider" style={{color:"var(--foreground)"}}>
                    {forecast.event.venueName}
                  </h3>
                  <div className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] font-semibold" style={{color:"var(--muted)"}}>
                    <span style={{color:"var(--foreground)"}}>{eventTypeLabel}</span><span>·</span>
                    <span>{(forecast.event.attendance/1000).toFixed(0)}k</span><span>·</span>
                    <span>{DOW_LABELS[forecast.event.dow]} {formatHour(forecast.event.startHour)}</span><span>·</span>
                    <span>{RAIN_LABELS[forecast.event.rain]}</span>
                  </div>
                </div>
                <div className="flex gap-1 rounded-lg border p-0.5" style={{borderColor:"var(--border)",background:"var(--panel-2)"}}>
                  {["allocation","accuracy"].map(t => (
                    <button key={t} onClick={() => setRightTab(t)}
                      className="rounded-md px-2.5 py-1 text-[11px] font-bold capitalize transition cursor-pointer"
                      style={{background:rightTab===t?"var(--accent)":"transparent",color:rightTab===t?"var(--accent-foreground)":"var(--muted)",boxShadow:rightTab===t?"0 1px 1px rgba(0,0,0,0.08)":"none"}}>
                      {t}
                    </button>
                  ))}
                </div>
              </div>
              {rightTab==="allocation"?<AllocationPanel plan={plan}/>:
              <div className="flex flex-col">
                <div className="mb-2 flex items-center gap-2">
                  <Target size={15} style={{color:"var(--foreground)"}}/>
                  <h3 className="text-[13px] font-semibold uppercase tracking-wide" style={{color:"var(--foreground)"}}>
                    Predicted vs actual (synthetic scatter)
                  </h3>
                </div>
                <p className="mb-2 text-[10.5px] leading-relaxed" style={{color:"var(--muted)"}}>
                  Synthetic scatter from the simulator. Real accuracy lives in the Debrief view.
                </p>
                <div className="h-48">
                  <ResponsiveContainer width="100%" height="100%">
                    <ScatterChart margin={{top:8,right:10,bottom:4,left:-20}}>
                      <CartesianGrid stroke="var(--border)" strokeDasharray="3 4"/>
                      <XAxis type="number" dataKey="x" domain={[0,100]} tick={{fill:"var(--muted)",fontSize:10,fontWeight:600}} tickLine={false} axisLine={{stroke:"var(--border)"}}/>
                      <YAxis type="number" dataKey="y" domain={[0,100]} tick={{fill:"var(--muted)",fontSize:10,fontWeight:600}} tickLine={false} axisLine={false} width={36}/>
                      <ZAxis range={[28,28]}/>
                      <ReferenceLine segment={[{x:0,y:0},{x:100,y:100}]} stroke="var(--muted)" strokeDasharray="4 4"/>
                      <Tooltip cursor={{strokeDasharray:"3 3"}} contentStyle={{background:"var(--panel)",border:"1px solid var(--border)",borderRadius:12,fontSize:12,color:"var(--foreground)"}} formatter={v=>[Number(v).toFixed(0)]}/>
                      <Scatter data={accuracyScatter} fill="#e85d04" fillOpacity={0.7}/>
                    </ScatterChart>
                  </ResponsiveContainer>
                </div>
              </div>}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
