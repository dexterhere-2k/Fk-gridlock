import { Routes, Route } from "react-router-dom";
import { Header } from "./components/Shared.jsx";
import Tour from "./components/Tour.jsx";
import LiveView from "./pages/LiveView.jsx";
import PredictView from "./pages/PredictView.jsx";
import AllocateView from "./pages/AllocateView.jsx";
import ScheduleView from "./pages/ScheduleView.jsx";
import DebriefView from "./pages/DebriefView.jsx";
import OpsView from "./pages/OpsView.jsx";

export default function App() {
  return (
    <div className="min-h-screen">
      <Header />
      <main className="mx-auto max-w-7xl px-4 py-4">
        <Routes>
          <Route path="/"          element={<LiveView />} />
          <Route path="/predict"   element={<PredictView />} />
          <Route path="/allocate"  element={<AllocateView />} />
          <Route path="/schedule"  element={<ScheduleView />} />
          <Route path="/debrief"   element={<DebriefView />} />
          <Route path="/ops"       element={<OpsView />} />
          <Route path="*"          element={<LiveView />} />
        </Routes>
      </main>
      <Tour />
    </div>
  );
}
