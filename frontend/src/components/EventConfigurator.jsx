// Event configurator form for the simulate command-center.
// All form controls are themed (cf-select, custom range, custom checkbox).
import { Play, History, CalendarDays, CloudRain, Users, Zap } from "lucide-react";

const DOW_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const RAIN_LABELS = ["Clear", "Light rain", "Heavy rain"];

function formatHour(h) {
  const hh = Math.floor(h);
  const mm = Math.round((h - hh) * 60);
  const period = hh >= 12 ? "PM" : "AM";
  const hh12 = hh % 12 === 0 ? 12 : hh % 12;
  return `${hh12}:${String(mm).padStart(2, "0")} ${period}`;
}

function SectionTitle({ children, right }) {
  return (
    <div className="mb-2 flex items-center justify-between">
      <h3 className="text-[12px] font-bold uppercase tracking-wider"
          style={{ color: "var(--foreground)" }}>
        {children}
      </h3>
      {right}
    </div>
  );
}

function Field({ label, children }) {
  return (
    <label className="block">
      <div className="mb-1 text-[12px] font-semibold" style={{ color: "var(--muted)" }}>
        {label}
      </div>
      {children}
    </label>
  );
}

function RangeField({ label, value, min, max, step, onChange, display }) {
  const pct = ((value - min) / (max - min)) * 100;
  return (
    <div>
      <div className="mb-1 flex items-center justify-between">
        <span className="text-[12px] font-semibold" style={{ color: "var(--muted)" }}>
          {label}
        </span>
        <span className="text-[12px] font-bold tabular-nums"
              style={{ color: "var(--foreground)" }}>
          {display}
        </span>
      </div>
      <div className="relative flex h-4 w-full items-center">
        <div className="absolute left-0 right-0 h-1 rounded-full"
             style={{ background: "var(--border)" }} />
        <div
          className="absolute left-0 h-1 rounded-full transition-all duration-75"
          style={{ width: `${pct}%`, background: "var(--accent)" }}
        />
        <input
          type="range"
          min={min} max={max} step={step} value={value}
          onChange={(e) => onChange(Number(e.target.value))}
          className="absolute inset-x-0 w-full"
        />
      </div>
    </div>
  );
}

function Segmented({ options, value, onChange }) {
  return (
    <div className="flex rounded-lg border p-0.5"
         style={{ borderColor: "var(--border)", background: "var(--panel-2)" }}>
      {options.map((o) => (
        <button
          key={o.value}
          onClick={() => onChange(o.value)}
          className="flex-1 rounded-md px-2 py-1 text-[12px] font-semibold transition cursor-pointer"
          style={{
            background: value === o.value ? "var(--accent)" : "transparent",
            color: value === o.value ? "var(--accent-foreground)" : "var(--muted)",
          }}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export default function EventConfigurator({
  scenario, venues, eventTypes, weatherOptions,
  loading, onChange, onRun, onLoadReplay,
  curatedEvents = [], compact = false,
}) {
  const venue = venues.find((v) => v.id === scenario.venueId);
  const maxAttendance = venue ? Math.round(venue.capacity * 1.25) : 100000;
  const ratio = venue ? Math.round((scenario.attendance / venue.capacity) * 100) : 0;

  return (
    <div className="flex h-full flex-col gap-3 overflow-y-auto pr-1"
         style={{ scrollbarWidth: "thin" }}>
      {!compact && (
        <div>
          <SectionTitle right={<Zap size={14} />}>Plan an event</SectionTitle>
          <p className="text-[11px] leading-relaxed font-medium"
             style={{ color: "var(--muted)" }}>
            Configure a scenario. The simulator forecasts per-corridor
            congestion across Bengaluru and recommends a deployment plan.
          </p>
        </div>
      )}

      <Field label="Venue">
        <select className="input"
                value={scenario.venueId}
                onChange={(e) => onChange({ venueId: e.target.value })}>
          {venues.map((v) => (
            <option key={v.id} value={v.id}>
              {v.name} ({(v.capacity / 1000).toFixed(0)}k)
            </option>
          ))}
        </select>
      </Field>

      <Field label="Event type">
        <select className="input"
                value={scenario.eventType}
                onChange={(e) => onChange({ eventType: e.target.value })}>
          {eventTypes.map((t) => (
            <option key={t.key} value={t.key}>{t.label}</option>
          ))}
        </select>
      </Field>

      <RangeField
        label="Expected attendance"
        value={scenario.attendance}
        min={1000}
        max={maxAttendance}
        step={1000}
        onChange={(v) => onChange({ attendance: v })}
        display={`${(scenario.attendance / 1000).toFixed(0)}k · ${ratio}% cap`}
      />

      <RangeField
        label="Start time"
        value={scenario.startHour}
        min={5.5} max={23} step={0.5}
        onChange={(v) => onChange({ startHour: v })}
        display={formatHour(scenario.startHour)}
      />

      <div className="grid grid-cols-2 gap-3">
        <Field label="Day">
          <select className="input" value={scenario.dow}
                  onChange={(e) => onChange({ dow: Number(e.target.value) })}>
            {DOW_LABELS.map((d, i) => (
              <option key={i} value={i}>{d}</option>
            ))}
          </select>
        </Field>
        <Field label="Duration">
          <select className="input" value={scenario.durationMin}
                  onChange={(e) => onChange({ durationMin: Number(e.target.value) })}>
            {[60, 90, 120, 150, 180, 210, 240, 300, 360].map((m) => (
              <option key={m} value={m}>
                {Math.floor(m / 60)}h {m % 60 ? `${m % 60}m` : ""}
              </option>
            ))}
          </select>
        </Field>
      </div>

      <Field label="Weather">
        <Segmented
          options={weatherOptions.map((w) => ({ label: w.label, value: w.value }))}
          value={scenario.rain}
          onChange={(v) => onChange({ rain: v })}
        />
      </Field>

      <label className="flex cursor-pointer items-center justify-between rounded-xl border px-3 py-2 transition"
             style={{ borderColor: "var(--border)", background: "var(--panel-2)" }}>
        <span className="flex items-center gap-2 text-[12px] font-semibold"
              style={{ color: "var(--muted)" }}>
          <CalendarDays size={14} /> Public holiday
        </span>
        <input
          type="checkbox" checked={scenario.isHoliday}
          onChange={(e) => onChange({ isHoliday: e.target.checked })}
        />
      </label>

      <RangeField
        label="Manpower budget (officers)"
        value={scenario.manpowerBudget}
        min={0} max={200} step={5}
        onChange={(v) => onChange({ manpowerBudget: v })}
        display={`${scenario.manpowerBudget}`}
      />

      {!compact && (
        <button onClick={onRun} disabled={loading}
          className="mt-1 flex items-center justify-center gap-2 rounded-xl px-4 py-2.5 text-[14px] font-extrabold transition disabled:opacity-50 cursor-pointer"
          style={{background:"var(--accent)",color:"var(--accent-foreground)",boxShadow:"0 1px 3px rgba(0,0,0,0.15)"}}>
          {loading?"Forecasting…":<><Play size={16} fill="currentColor"/> Run forecast &amp; plan</>}
        </button>
      )}

      {!compact && curatedEvents.length > 0 && (
        <div className="mt-2 border-t pt-3" style={{ borderColor: "var(--border)" }}>
          <SectionTitle right={<History size={14} />}>Replay real events</SectionTitle>
          <p className="mb-2 text-[11px] leading-relaxed font-medium"
             style={{ color: "var(--muted)" }}>
            Post-event learning: replay a real Bengaluru event scenario.
          </p>
          <div className="flex flex-col gap-1.5">
            {curatedEvents.map((h) => (
              <button
                key={h.id}
                onClick={() => onLoadReplay(h.id)}
                className="rounded-xl border px-3 py-2 text-left transition cursor-pointer"
                style={{
                  borderColor: "var(--border)",
                  background: "var(--panel-2)",
                }}
                onMouseEnter={(e) => e.currentTarget.style.borderColor = "var(--accent)"}
                onMouseLeave={(e) => e.currentTarget.style.borderColor = "var(--border)"}
              >
                <div className="text-[12px] font-bold" style={{ color: "var(--foreground)" }}>
                  {h.name}
                </div>
                <div className="mt-0.5 flex items-center gap-2 text-[10.5px] font-medium"
                     style={{ color: "var(--muted)" }}>
                  <span>{h.date}</span>
                  <span className="flex items-center gap-1">
                    <Users size={10} /> {(h.attendance / 1000).toFixed(0)}k
                  </span>
                  {h.rain > 0 && (
                    <span className="flex items-center gap-1">
                      <CloudRain size={10} /> rain
                    </span>
                  )}
                </div>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
