// 7-step onboarding tour for 5 tabs. Each step targets a DOM element via
// [data-tour="<id>"]. `route` is navigated to before measurement.
export const TOUR_STORAGE_KEY = "nexgen:tour:done:v1";
export const TOUR_EVENT = "nexgen:tour:start";

export const TOUR_STEPS = [
  {
    id: "welcome",
    route: "/",
    target: "header",
    placement: "bottom",
    title: "Welcome to NexGen",
    body:
      "ASTraM intelligence layer — forecasting event traffic impact, " +
      "optimising deployment, and learning from every outcome. " +
      "Press Esc anytime to skip.",
  },
  {
    id: "nav",
    route: "/",
    target: "nav",
    placement: "bottom",
    title: "Five tabs, one pipeline",
    body:
      "Live → Predict → Allocate → Simulate → Debrief. " +
      "The same incident flows through the entire dispatch pipeline.",
  },
  {
    id: "map",
    route: "/",
    target: "map",
    placement: "right",
    title: "Live corridor status",
    body:
      "22 Bengaluru corridors colour-coded by historical risk. " +
      "Cascade edges (yellow dashed) connect corridors that trigger " +
      "downstream traffic. WebSocket pulses replay real ASTraM incidents " +
      "as if they were happening now. Hover any dot for stats.",
  },
  {
    id: "timeline",
    route: "/",
    target: "timeline",
    placement: "top",
    title: "Historical incident timeline",
    body:
      "24-hour replay of real ASTraM incident counts. Drag the slider " +
      "to scrub through the day — morning and evening peaks show the " +
      "most activity. The chart below shows average and peak congestion.",
  },
  {
    id: "predict",
    route: "/predict",
    target: "predict-form",
    placement: "right",
    title: "Predict clearance time",
    body:
      "Score a single incident. Type a corridor, cause, and free-text " +
      "description (Kannada works too). Get a [P10, P50, P90] clearance " +
      "band with a confidence chip, closure probability, and NLP-parsed " +
      "cues. Try the Kannada bus sample!\n\n" +
      "• P10 = best case, P50 = median, P90 = worst case.\n" +
      "• Wide band = low confidence → defer to operator.\n" +
      "• Because: lists the key factors driving the prediction.",
  },
  {
    id: "allocate",
    route: "/allocate",
    target: "allocate",
    placement: "left",
    title: "Allocate resources",
    body:
      "Jointly optimise officers + barricades across multiple concurrent " +
      "events under one shared budget. The ILP solver (PuLP) finds the " +
      "optimal deployment plan — not greedy per-event.\n\n" +
      "• Add events with corridor, cause, and estimated clearance.\n" +
      "• Set the available officer pool and hit Run ILP.\n" +
      "• Cascade pre-positioning reserves a standby unit on the strongest " +
      "downstream corridor before the spillover arrives.\n" +
      "• Re-run with different officer counts to compare plans.",
  },
  {
    id: "debrief",
    route: "/debrief",
    target: "debrief",
    placement: "top",
    title: "Debrief & learning loop",
    body:
      "Post-event review that closes the learning loop. Compare predicted " +
      "vs actual clearance times, log outcomes to the SQLite ledger, and " +
      "retrain the model when it drifts.\n\n" +
      "• Top KPIs: outcomes logged, P50 MAE, closure accuracy.\n" +
      "• Look up any prediction ID for plan-vs-actual variance.\n" +
      "• The learning signal table shows per-cause model drift.\n" +
      "• Retrain Model button activates when 25+ outcomes accumulate with " +
      "significant MAE drift. This actually retrains — not a mock.\n" +
      "• This is the closed loop that ASTraM doesn't have today.",
  },
];
