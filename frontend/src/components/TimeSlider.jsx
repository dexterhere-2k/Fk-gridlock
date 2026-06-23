// Time slider with play/pause + peak-jump buttons
// and a native <input type="range"> with a peak marker overlay.
//
// Performance: the onScrub callback is throttled to one call per
// requestAnimationFrame so dragging the slider fires at most ~60 React
// renders/sec (browsers cap rAF at the display refresh rate). The
// native range thumb still tracks the mouse 1:1 visually.
import { useEffect, useRef } from "react";
import { Pause, Play, SkipForward } from "lucide-react";

export default function TimeSlider({
  timeline, timeIndex, peakIndex, playing,
  onScrub, onPlayToggle, onJumpPeak,
  labelFormatter,
}) {
  // rAF throttle for the scrub callback
  const rafRef = useRef(null);
  const pendingRef = useRef(null);
  useEffect(() => () => {
    if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
  }, []);

  const flushScrub = () => {
    rafRef.current = null;
    if (pendingRef.current != null) {
      const v = pendingRef.current;
      pendingRef.current = null;
      onScrub(v);
    }
  };

  const handleScrub = (v) => {
    pendingRef.current = v;
    if (rafRef.current == null) {
      rafRef.current = requestAnimationFrame(flushScrub);
    }
  };

  if (!timeline || timeline.length === 0) return null;
  const bucket = timeline[timeIndex] || timeline[0];
  const peak = peakIndex != null && peakIndex >= 0 && peakIndex < timeline.length
    ? timeline[peakIndex] : null;
  const labelText = labelFormatter
    ? labelFormatter(bucket, timeIndex)
    : `${bucket.label}`;
  return (
    <div className="card p-3">
      <div className="flex items-center gap-3">
        <button
          onClick={onPlayToggle}
          className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-accent-600 text-white shadow-md transition hover:opacity-90"
          aria-label={playing ? "Pause" : "Play"}
          title={playing ? "Pause playback" : "Play timeline"}
        >
          {playing ? <Pause size={14} fill="currentColor" /> : <Play size={14} fill="currentColor" />}
        </button>
        {peak && (
          <button
            onClick={onJumpPeak}
            className="flex h-8 shrink-0 items-center gap-1 rounded-full border border-ink-700 bg-ink-900 px-3 text-[11px] font-bold text-ink-200 transition hover:bg-ink-800"
            title="Jump to peak impact"
          >
            <SkipForward size={12} /> Peak
          </button>
        )}
        <div className="min-w-0 flex-1">
          <div className="mb-1 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className="text-[14px] font-extrabold tabular-nums text-ink-50">
                {labelText}
              </span>
              <span className="rounded-full border border-ink-700 bg-ink-900 px-2 py-0.5 text-[10px] font-bold text-ink-200">
                {bucket.phase || "history"}
              </span>
            </div>
            <span className="text-[11px] text-ink-400 font-bold">
              avg <b className="text-ink-100">{bucket.avgCongestion.toFixed(0)}</b> ·
              peak <b className="text-ink-100">{bucket.maxCongestion.toFixed(0)}</b>
            </span>
          </div>
          <div className="relative">
            <input
              type="range"
              min={0}
              max={timeline.length - 1}
              step={1}
              value={timeIndex}
              onChange={(e) => handleScrub(Number(e.target.value))}
              className="w-full"
            />
            {peak && (
              <div
                className="pointer-events-none absolute -top-0.5 h-3 w-0.5 bg-ink-200"
                style={{
                  left: `${(peakIndex / (timeline.length - 1)) * 100}%`,
                }}
                title="Peak impact"
              />
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
