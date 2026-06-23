// Area chart for the Live-view timeline scrubber.
// Renders two series (peak + avg) and a current-time reference line.
// Click anywhere on the chart to scrub to that time index.
//
// Props:
//   timeline:  [{ minutes, label, avgCongestion, maxCongestion, n_incidents }, ...]
//   timeIndex: current scrub index
//   onScrub:   (i) => void
//   height:    px (default 120)
import {
  AreaChart, Area, CartesianGrid, XAxis, YAxis, ReferenceLine,
  ResponsiveContainer, Tooltip,
} from "recharts";

export default function TimelineChart({ timeline, timeIndex, onScrub, height = 120 }) {
  if (!timeline || timeline.length === 0) {
    return (
      <div className="flex h-32 items-center justify-center text-xs text-ink-500">
        no timeline data
      </div>
    );
  }
  const data = timeline.map((b, i) => ({
    i, label: b.label, avg: b.avgCongestion, peak: b.maxCongestion,
    n: b.n_incidents ?? 0,
  }));
  const current = data[timeIndex] || data[0];
  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart
        data={data}
        margin={{ top: 6, right: 8, left: -22, bottom: 0 }}
        onClick={(e) => {
          if (onScrub && e && typeof e.activeTooltipIndex === "number") {
            onScrub(e.activeTooltipIndex);
          }
        }}
      >
        <defs>
          <linearGradient id="gPeak" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%"   stopColor="#e85d04" stopOpacity={0.28} />
            <stop offset="100%" stopColor="#e85d04" stopOpacity={0} />
          </linearGradient>
          <linearGradient id="gAvg" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%"   stopColor="#1f7af8" stopOpacity={0.20} />
            <stop offset="100%" stopColor="#1f7af8" stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid stroke="#262626" strokeDasharray="3 4" vertical={false} />
        <XAxis
          dataKey="label"
          tick={{ fill: "#8d8980", fontSize: 10, fontWeight: 600 }}
          interval={Math.max(1, Math.floor(data.length / 7))}
          tickLine={false}
          axisLine={{ stroke: "#262626" }}
        />
        <YAxis
          domain={[0, 100]}
          tick={{ fill: "#8d8980", fontSize: 10, fontWeight: 600 }}
          tickLine={false}
          axisLine={false}
          width={34}
        />
        <Tooltip
          contentStyle={{
            background: "#141312",
            border: "1px solid #262421",
            borderRadius: 12,
            fontSize: 12,
            color: "#f5f4f2",
          }}
          labelStyle={{ color: "#f5f4f2", fontWeight: "bold" }}
          formatter={(v, n) => [Number(v).toFixed(0), n === "peak" ? "Peak" : "Avg"]}
        />
        {current && (
          <ReferenceLine x={current.label} stroke="#e85d04" strokeWidth={2} />
        )}
        <Area
          type="monotone"
          dataKey="peak"
          name="peak"
          stroke="#e85d04"
          fill="url(#gPeak)"
          strokeWidth={2}
        />
        <Area
          type="monotone"
          dataKey="avg"
          name="avg"
          stroke="#1f7af8"
          fill="url(#gAvg)"
          strokeWidth={2}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
