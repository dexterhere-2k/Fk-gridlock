// Driver-style multi-step tour. Custom (no external lib) so the SPA stays a
// single deployable bundle. Renders into a portal at document.body to avoid
// z-index / overflow traps with the sticky header.
//
// Mechanics:
//   - Backdrop = 4 black/60 divs framing the target's bounding rect (a hole-
//     punched cutout). Cheap, no SVG masking, and re-flows on scroll/resize.
//   - Popover is positioned next to the target with placement auto-flipped
//     when it would overflow the viewport.
//   - Multi-page steps: after Back/Next we navigate to the step's route and
//     wait one frame for the new page to mount before measuring the target.
//   - Persistence via localStorage. The "?" button in the header dispatches
//     TOUR_EVENT to replay the tour at any time.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useLocation, useNavigate } from "react-router-dom";
import { TOUR_EVENT, TOUR_STEPS, TOUR_STORAGE_KEY } from "../lib/tourSteps.js";

const GAP = 12;       // px between spotlight and popover
const PADDING = 6;    // px of breathing room around the target
const POPOVER_W = 360;
const POPOVER_MAX_H = 360;

function readDone() {
  try {
    return localStorage.getItem(TOUR_STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

function writeDone() {
  try {
    localStorage.setItem(TOUR_STORAGE_KEY, "1");
  } catch {}
}

function clearDone() {
  try {
    localStorage.removeItem(TOUR_STORAGE_KEY);
  } catch {}
}

function prefersReducedMotion() {
  if (typeof window === "undefined") return false;
  return window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
}

function getRect(target) {
  const r = target.getBoundingClientRect();
  return {
    top: r.top - PADDING,
    left: r.left - PADDING,
    width: r.width + PADDING * 2,
    height: r.height + PADDING * 2,
    right: r.right + PADDING,
    bottom: r.bottom + PADDING,
  };
}

function computePopover(rect, placement, vw, vh) {
  const popH = POPOVER_MAX_H; // estimated; real height comes from layout
  let top = 0;
  let left = 0;
  let actual = placement;

  switch (placement) {
    case "top":
      top = rect.top - popH - GAP;
      left = rect.left + rect.width / 2 - POPOVER_W / 2;
      if (top < 8) { actual = "bottom"; top = rect.top + rect.height + GAP; }
      break;
    case "bottom":
      top = rect.top + rect.height + GAP;
      left = rect.left + rect.width / 2 - POPOVER_W / 2;
      if (top + popH > vh - 8) { actual = "top"; top = rect.top - popH - GAP; }
      break;
    case "left":
      top = rect.top + rect.height / 2 - popH / 2;
      left = rect.left - POPOVER_W - GAP;
      if (left < 8) { actual = "right"; left = rect.left + rect.width + GAP; }
      break;
    case "right":
    default:
      top = rect.top + rect.height / 2 - popH / 2;
      left = rect.left + rect.width + GAP;
      if (left + POPOVER_W > vw - 8) { actual = "left"; left = rect.left - POPOVER_W - GAP; }
      break;
  }

  // Clamp inside viewport
  left = Math.max(8, Math.min(vw - POPOVER_W - 8, left));
  top = Math.max(8, Math.min(vh - 100, top));

  return { top, left, actual };
}

export default function Tour() {
  const navigate = useNavigate();
  const location = useLocation();
  const [active, setActive] = useState(false);
  const [stepIndex, setStepIndex] = useState(0);
  const [rect, setRect] = useState(null);
  const [target, setTarget] = useState(null);
  const [popover, setPopover] = useState(null);
  const popoverRef = useRef(null);
  const lastFocusRef = useRef(null);

  const step = TOUR_STEPS[stepIndex];
  const total = TOUR_STEPS.length;

  const measure = useCallback(() => {
    if (!step) return;
    const el = document.querySelector(`[data-tour="${step.target}"]`);
    if (!el) {
      setTarget(null);
      setRect(null);
      setPopover(null);
      return;
    }
    setTarget(el);
    el.scrollIntoView({ behavior: prefersReducedMotion() ? "auto" : "smooth", block: "center" });
    const r = getRect(el);
    setRect(r);
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const p = computePopover(r, step.placement || "right", vw, vh);
    setPopover(p);
  }, [step]);

  const start = useCallback((fromStep = 0) => {
    clearDone();
    setStepIndex(fromStep);
    setActive(true);
    lastFocusRef.current = document.activeElement;
  }, []);

  const stop = useCallback((markDone = true) => {
    if (markDone) writeDone();
    setActive(false);
    setRect(null);
    setTarget(null);
    setPopover(null);
    if (lastFocusRef.current && typeof lastFocusRef.current.focus === "function") {
      lastFocusRef.current.focus();
    }
  }, []);

  const next = useCallback(() => {
    if (stepIndex >= total - 1) {
      stop(true);
      return;
    }
    setStepIndex((i) => i + 1);
  }, [stepIndex, total, stop]);

  const back = useCallback(() => {
    setStepIndex((i) => Math.max(0, i - 1));
  }, []);

  // External trigger (header "?" button)
  useEffect(() => {
    const handler = () => start(0);
    window.addEventListener(TOUR_EVENT, handler);
    return () => window.removeEventListener(TOUR_EVENT, handler);
  }, [start]);

  // Auto-start on first visit
  useEffect(() => {
    if (readDone()) return;
    const t = setTimeout(() => start(0), 600);
    return () => clearTimeout(t);
  }, [start]);

  // Navigate when step changes
  useEffect(() => {
    if (!active || !step) return;
    if (location.pathname !== step.route) {
      navigate(step.route, { replace: false });
    }
  }, [active, step, location.pathname, navigate]);

  // Measure after route change + paint
  useEffect(() => {
    if (!active) return;
    // two RAFs: one for the route change, one for the new page's paint
    let raf2;
    const raf1 = requestAnimationFrame(() => {
      raf2 = requestAnimationFrame(() => measure());
    });
    return () => {
      cancelAnimationFrame(raf1);
      if (raf2) cancelAnimationFrame(raf2);
    };
  }, [active, stepIndex, location.pathname, measure]);

  // Re-measure on scroll/resize
  useEffect(() => {
    if (!active) return;
    const onChange = () => measure();
    window.addEventListener("resize", onChange);
    window.addEventListener("scroll", onChange, true);
    return () => {
      window.removeEventListener("resize", onChange);
      window.removeEventListener("scroll", onChange, true);
    };
  }, [active, measure]);

  // Keyboard nav
  useEffect(() => {
    if (!active) return;
    const onKey = (e) => {
      if (e.key === "Escape") { e.preventDefault(); stop(true); }
      else if (e.key === "ArrowRight") { e.preventDefault(); next(); }
      else if (e.key === "ArrowLeft")  { e.preventDefault(); back(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [active, next, back, stop]);

  // Focus trap into popover when it mounts
  useEffect(() => {
    if (!active || !popover) return;
    const t = setTimeout(() => {
      popoverRef.current?.querySelector("button")?.focus();
    }, 0);
    return () => clearTimeout(t);
  }, [active, popover]);

  const isLast = stepIndex === total - 1;
  const motion = useMemo(() => prefersReducedMotion(), [active]);

  if (!active || !step) return null;
  if (typeof document === "undefined") return null;

  const vw = typeof window !== "undefined" ? window.innerWidth : 1024;
  const vh = typeof window !== "undefined" ? window.innerHeight : 768;

  return createPortal(
    <div
      className="fixed inset-0 z-50"
      role="dialog"
      aria-modal="true"
      aria-labelledby="tour-title"
      aria-describedby="tour-body"
    >
      {/* Backdrop: 4 dark divs framing the target. Skipped if target missing. */}
      {rect ? (
        <>
          <div
            className="absolute bg-ink-950/70"
            style={{ top: 0, left: 0, right: 0, height: Math.max(0, rect.top) }}
          />
          <div
            className="absolute bg-ink-950/70"
            style={{ top: rect.top, left: 0, width: Math.max(0, rect.left), height: rect.height }}
          />
          <div
            className="absolute bg-ink-950/70"
            style={{
              top: rect.top,
              left: rect.left + rect.width,
              right: 0,
              height: rect.height,
            }}
          />
          <div
            className="absolute bg-ink-950/70"
            style={{
              top: rect.top + rect.height,
              left: 0,
              right: 0,
              bottom: 0,
            }}
          />
        </>
      ) : (
        <div className="absolute inset-0 bg-ink-950/70" />
      )}

      {/* Spotlight ring (visual hint, not a clip) */}
      {rect ? (
        <div
          className="pointer-events-none absolute rounded-lg ring-2 ring-accent-500 ring-offset-2 ring-offset-ink-950 shadow-glow"
          style={{
            top: rect.top,
            left: rect.left,
            width: rect.width,
            height: rect.height,
            transition: motion ? "none" : "all 200ms ease-out",
          }}
        />
      ) : null}

      {/* Missing target hint */}
      {rect === null ? (
        <div
          className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 card p-4 text-sm text-ink-200"
          style={{ width: POPOVER_W }}
        >
          <div className="font-medium text-ink-50">Tour step unavailable</div>
          <div className="mt-1 text-ink-400">
            Couldn't find <code className="font-mono text-accent-400">data-tour="{step.target}"</code> on this page.
          </div>
          <div className="mt-3 flex justify-end gap-2">
            <button onClick={() => stop(true)} className="rounded px-3 py-1.5 text-xs text-ink-300 hover:text-ink-100">
              Skip tour
            </button>
            <button onClick={next} className="rounded bg-accent-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-accent-500">
              {isLast ? "Done" : "Next"}
            </button>
          </div>
        </div>
      ) : null}

      {/* Popover */}
      {popover ? (
        <div
          ref={popoverRef}
          className="absolute card border-ink-700 bg-ink-900 p-4 shadow-2xl"
          style={{
            top: popover.top,
            left: popover.left,
            width: POPOVER_W,
            maxHeight: POPOVER_MAX_H,
            transition: motion ? "none" : "opacity 180ms ease-out, transform 180ms ease-out",
          }}
        >
          <div className="flex items-start justify-between gap-2">
            <div id="tour-title" className="text-sm font-semibold text-ink-50">
              {step.title}
            </div>
            <button
              onClick={() => stop(true)}
              aria-label="Skip tour"
              className="-mt-1 -mr-1 rounded p-1 text-ink-400 hover:bg-ink-800 hover:text-ink-100"
            >
              <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                <path d="M2 2 L12 12 M12 2 L2 12" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
              </svg>
            </button>
          </div>
          <div
            id="tour-body"
            className="mt-2 overflow-auto whitespace-pre-line text-[13px] leading-relaxed text-ink-300"
            style={{ maxHeight: POPOVER_MAX_H - 110 }}
          >
            {step.body}
          </div>
          <div className="mt-3 flex items-center justify-between">
            <div className="flex items-center gap-1.5">
              {TOUR_STEPS.map((s, i) => (
                <button
                  key={s.id}
                  onClick={() => setStepIndex(i)}
                  aria-label={`Go to step ${i + 1}`}
                  className={
                    "h-1.5 rounded-full transition-all " +
                    (i === stepIndex
                      ? "w-6 bg-accent-500"
                      : i < stepIndex
                      ? "w-1.5 bg-ink-600 hover:bg-ink-500"
                      : "w-1.5 bg-ink-700 hover:bg-ink-600")
                  }
                />
              ))}
            </div>
            <div className="flex items-center gap-1">
              <span className="mr-1 font-mono text-[10px] text-ink-500">
                {stepIndex + 1}/{total}
              </span>
              <button
                onClick={() => stop(true)}
                className="rounded px-2.5 py-1 text-xs text-ink-300 hover:bg-ink-800 hover:text-ink-100"
              >
                Skip
              </button>
              <button
                onClick={back}
                disabled={stepIndex === 0}
                className="rounded px-2.5 py-1 text-xs text-ink-300 hover:bg-ink-800 hover:text-ink-100 disabled:opacity-30"
              >
                Back
              </button>
              <button
                onClick={next}
                className="rounded bg-accent-600 px-2.5 py-1 text-xs font-medium text-white hover:bg-accent-500"
              >
                {isLast ? "Done" : "Next"}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>,
    document.body
  );
}
