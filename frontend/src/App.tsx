import { Routes, Route, Navigate } from "react-router-dom";
import { AppShell } from "./components/shell/AppShell";
import FeedPage from "./routes/feed/FeedPage";
import GraphPage from "./routes/graph/GraphPage";
import EvidencePage from "./routes/evidence/EvidencePage";
import VoicePage from "./routes/voice/VoicePage";
import IngestPage from "./routes/ingest/IngestPage";
import SettingsPage from "./routes/settings/SettingsPage"; 
import LoginPage from "./routes/login/LoginPage";
import RegisterPage from "./routes/register/RegisterPage";
import { ProtectedRoute } from "./components/ProtectedRoute";

export default function App() {
  return (
    <Routes> 
      <Route path="/login" element={<LoginPage />} />
<Route path="/register" element={<RegisterPage />} />
<Route path="/app" element={<ProtectedRoute />}>
  <Route element={<AppShell />}>
    {/* existing /feed, /graph, etc. routes, now nested under /app */}
  </Route>
</Route>
      <Route element={<AppShell />}>
        <Route index element={<Navigate to="/feed" replace />} />
        <Route path="/feed" element={<FeedPage />} />
        <Route path="/graph" element={<GraphPage />} />
        <Route path="/evidence" element={<EvidencePage />} />
        <Route path="/voice" element={<VoicePage />} />
        <Route path="/ingest" element={<IngestPage />} />
        <Route path="/settings" element={<SettingsPage />} />
      </Route>
    </Routes>
  );
} 
