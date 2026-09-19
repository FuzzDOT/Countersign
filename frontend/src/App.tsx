import { Routes, Route, Navigate } from "react-router-dom";
import { AppShell } from "./components/shell/AppShell";
import FeedPage from "./routes/feed/FeedPage";
import GraphPage from "./routes/graph/GraphPage";
import EvidencePage from "./routes/evidence/EvidencePage";
import VoicePage from "./routes/voice/VoicePage";
import IngestPage from "./routes/ingest/IngestPage";
import SettingsPage from "./routes/settings/SettingsPage";

export default function App() {
  return (
    <Routes>
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