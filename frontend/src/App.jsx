import { Routes, Route } from "react-router-dom";
import { Header } from "./components/Shared.jsx";
import Tour from "./components/Tour.jsx";
import LiveView from "./pages/LiveView.jsx";
import PredictView from "./pages/PredictView.jsx";
import AllocateView from "./pages/AllocateView.jsx";
import DebriefView from "./pages/DebriefView.jsx";
import SimulateView from "./pages/SimulateView.jsx";

export default function App() {
  return (
    <div className="flex h-screen flex-col">
      <Header />
      <main className="flex-1 min-h-0 overflow-hidden">
        <Routes>
          <Route path="/"          element={<LiveView />} />
          <Route path="/predict"   element={<PredictView />} />
          <Route path="/allocate"  element={<AllocateView />} />
          <Route path="/simulate"  element={<SimulateView />} />
          <Route path="/debrief"   element={<DebriefView />} />
          <Route path="*"          element={<LiveView />} />
        </Routes>
      </main>
      <Tour />
    </div>
  );
}
