// 8-step onboarding tour. Each step targets a DOM element via [data-tour="<id>"].
// `route` is navigated to before the target is measured. `placement` is a hint
// for where the popover sits relative to the target; the tour auto-flips when
// the popover would overflow the viewport.

export const TOUR_STORAGE_KEY = "gridlock:tour:done:v1";
export const TOUR_EVENT = "gridlock:tour:start";

export const TOUR_STEPS = [
  {
    id: "welcome",
    route: "/",
    target: "header",
    placement: "bottom",
    title: "Welcome to GridLock",
    body:
      "ASTraM Command Center — the Bengaluru traffic dispatch copilot. " +
      "This 45-second tour walks through all 6 views. Press Esc anytime to skip.",
  },
  {
    id: "map",
    route: "/",
    target: "map",
    placement: "right",
    title: "Live corridor map",
    body:
      "Corridor risk heatmap on top, cascade edges in orange, and live event " +
      "pulses streamed over WebSocket. Hover a corridor to see its P10/P50/P90 " +
      "clearance band.",
  },
  {
    id: "nav",
    route: "/",
    target: "nav",
    placement: "bottom",
    title: "Six views, one workflow",
    body:
      "Live → Predict → Allocate → Schedule → Ops → Debrief. The same incident " +
      "flows left to right through the dispatch pipeline.",
  },
  {
    id: "predict",
    route: "/predict",
    target: "predict-form",
    placement: "right",
    title: "Predict clearance time",
    body:
      "Type a free-text incident (or click a sample — try the Kannada bus one). " +
      "Get a [P10, P90] range with a confidence chip and a tier label.\n\n" +
      "• Fill corridor + event_cause (vehicle_breakdown, tree_fall, accident, vip_movement, …).\n" +
      "• Drop a natural-language description — the NLP layer parses it for veh_type, " +
      "lane block count, and language (English / Kannada / Hindi).\n" +
      "• Toggle is_planned for barricades + scheduled closures.\n" +
      "• Press Run prediction. The band is your [P10, P50, P90] clearance window in minutes.\n" +
      "• Confidence = high / medium / low based on the model's posterior. Low confidence " +
      "→ don't trust the point estimate, trust the band width.\n" +
      "• Try the Kannada bus sample to see multilingual parsing in action.",
  },
  {
    id: "allocate",
    route: "/allocate",
    target: "allocate",
    placement: "left",
    title: "Allocate officers",
    body:
      "Recommended dispatch sorted by ETA and station load. Each row shows the " +
      "matching confidence chip inherited from the prediction step.\n\n" +
      "• Set Officers = the available pool size (5–200), then Run ILP.\n" +
      "• The solver (PuLP ILP) jointly minimizes expected congestion-minutes across " +
      "all concurrent events under one shared budget — it's not greedy per-event.\n" +
      "• Top section: concurrent events ranked by total need. Each row is one event.\n" +
      "• Bottom section: per-officer dispatch assignments with station, ETA, and " +
      "the event tier (HIGH / MED / LOW).\n" +
      "• Cascade pre-positioning: officers are pre-staged near the top cascade edges " +
      "even before the secondary event fires — this is the spec's \"stage before you need\" heuristic.\n" +
      "• Re-run with a different officer count to see how the plan shifts.",
  },
  {
    id: "schedule",
    route: "/schedule",
    target: "schedule",
    placement: "top",
    title: "Plan a shift",
    body:
      "Drag officers onto corridors and time blocks. Conflicts light up red; " +
      "coverage gaps light up amber.\n\n" +
      "• Pick a planned event (VIP convoy, marathon, procession, etc.) from the chips.\n" +
      "• The timeline below shows T-120 (barricades go up), T-30 (officers deploy), " +
      "and P50 (demob at predicted median clearance).\n" +
      "• Each row = one officer; columns = time blocks. Drop a chip onto a cell to " +
      "assign them to that corridor at that time.\n" +
      "• Red = double-booking (one officer, two corridors in the same block).\n" +
      "• Amber = coverage gap (a corridor is under-staffed for its tier).\n" +
      "• The plan auto-validates against minimum coverage per tier; if a HIGH-tier " +
      "corridor is under-covered at deploy time, the row gets a warning badge.",
  },
  {
    id: "ops",
    route: "/ops",
    target: "ops",
    placement: "left",
    title: "What-if scenarios",
    body:
      "Run side-by-side comparisons and trigger re-optimization. Health and " +
      "slo badges at the top reflect the current API + model state.\n\n" +
      "• The state machine strip at the top walks DETECT → PREDICT → ALLOCATE → " +
      "DISPATCH → RESOLVE. The active state is highlighted in accent color.\n" +
      "• Pick an incident in the list to see its full timeline + current cascade pre-alerts.\n" +
      "• Run scenario: forks the current plan with a what-if parameter (extra event, " +
      "officer down, corridor closure) and shows predicted delta in clearance time.\n" +
      "• ↻ Retrain: kicks the learning loop with the latest debrief outcomes. " +
      "Use sparingly — it actually retrains, not a mock.\n" +
      "• Cascade pre-alert toast: pops up when a secondary event is predicted on a " +
      "top-3 cascade edge from an active HIGH-tier event — that's the cue to pre-stage.\n" +
      "• SLO badges: p50 inference latency, prediction throughput, last train timestamp.",
  },
  {
    id: "debrief",
    route: "/debrief",
    target: "debrief",
    placement: "top",
    title: "Post-incident review",
    body:
      "Predicted vs. actual clearance, cascade attribution, and the model " +
      "drift signal. This is what trains the next iteration.\n\n" +
      "• Top KPI strip: P50 MAE (median absolute error, in minutes), closure accuracy " +
      "(predicted closure vs. actual), outcomes logged, API status.\n" +
      "• Look up a debrief: enter an event_id and press Load. You get the full record — " +
      "predicted P10/P50/P90, actual clearance, NLP cues the model picked up, and " +
      "which cascade edges actually fired.\n" +
      "• Plan-vs-actual variance chart: scatter of predicted P50 vs. actual minutes, " +
      "with the y=x line as ground truth. Points above the line = under-predicted " +
      "(incident took longer than forecast).\n" +
      "• Accuracy trend line: rolling MAE over the last N outcomes. Drift up = model " +
      "is degrading, time to retrain (or revisit features).\n" +
      "• Cascade attribution: for each HIGH-tier event, which downstream corridors " +
      "saw measurable slowdown, and the predicted vs. observed propagation lag.",
  },
];
