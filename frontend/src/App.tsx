import { Routes, Route, Navigate } from "react-router-dom";
import { AppShell } from "./components/shell/AppShell";
import { ProtectedRoute } from "./components/ProtectedRoute";

import LandingPage from "./routes/Landing/LandingPage";
import LoginPage from "./routes/login/LoginPage";
import RegisterPage from "./routes/register/RegisterPage";

import FeedPage from "./routes/feed/FeedPage";
import GraphPage from "./routes/graph/GraphPage";
import EvidencePage from "./routes/evidence/EvidencePage";
import VoicePage from "./routes/voice/VoicePage";
import IngestPage from "./routes/ingest/IngestPage";
import SettingsPage from "./routes/settings/SettingsPage";

export default function App() {
  return (
    <Routes>
      {/* Marketing surface */}
      <Route path="/" element={<LandingPage />} />
      <Route path="/login" element={<LoginPage />} />
      <Route path="/register" element={<RegisterPage />} />

      {/* Application surface, gated behind auth, per the §3 route map */}
      <Route path="/app" element={<ProtectedRoute />}>
        <Route element={<AppShell />}>
          <Route index element={<Navigate to="/app/feed" replace />} />
          <Route path="feed" element={<FeedPage />} />
          <Route path="graph" element={<GraphPage />} />
          <Route path="evidence" element={<EvidencePage />} />
          <Route path="voice" element={<VoicePage />} />
          <Route path="ingest" element={<IngestPage />} />
          <Route path="settings" element={<SettingsPage />} />
        </Route>
      </Route>
    </Routes>
  );
}