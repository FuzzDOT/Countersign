import { lazy, Suspense } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { RequireAuth } from "./auth/RequireAuth";
import { AppShell } from "./components/shell/AppShell";
import { Skeleton } from "./components/primitives/Skeleton";
import IngestPage from "./routes/ingest/IngestPage";
import SettingsPage from "./routes/settings/SettingsPage";

import LandingPage from "./routes/Landing/LandingPage";
import LoginPage from "./routes/login/LoginPage";
import RegisterPage from "./routes/register/RegisterPage";

// Frontend dev 2 screens are code-split so d3 (graph chunk) and Recharts
// (evidence chunk) stay out of the landing bundle (brief section 16).
const FeedRoute = lazy(() => import("./routes/app/FeedRoute"));
const GraphRoute = lazy(() => import("./routes/app/GraphRoute"));
const DocumentRoute = lazy(() => import("./routes/app/DocumentRoute"));
const EvidenceRoute = lazy(() => import("./routes/app/EvidenceRoute"));
const VoiceRoute = lazy(() => import("./routes/app/VoiceRoute"));

/** Old shell paths (/feed) forward to the brief route map (/app/feed), keeping the query string. */
function LegacyRedirect({ to }: { to: string }) {
  const { search } = useLocation();
  return <Navigate to={`${to}${search}`} replace />;
}

/**
 * Route map (brief section 3). "/" is the marketing landing page, /login and
 * /register are the real auth screens, everything else lives under /app/*.
 */
export default function App() {
  return (
    <Suspense fallback={<Skeleton className="m-6 h-10 w-64" />}>
      <Routes>
        <Route path="/" element={<LandingPage />} />
        <Route path="/login" element={<LoginPage />} />
        <Route path="/register" element={<RegisterPage />} />

        <Route path="/app" element={<RequireAuth />}>
          <Route element={<AppShell />}>
            <Route index element={<Navigate to="/app/feed" replace />} />
            {/* Optional segments keep the list mounted when a sheet or entity opens. */}
            <Route path="feed/:insightId?" element={<FeedRoute />} />
            <Route path="graph/:entityId?" element={<GraphRoute />} />
            <Route path="document/:docId" element={<DocumentRoute />} />
            <Route path="evidence" element={<EvidenceRoute />} />
            <Route path="voice" element={<VoiceRoute />} />
            <Route path="ingest" element={<IngestPage />} />
            <Route path="settings" element={<SettingsPage />} />
          </Route>
        </Route>

        {["feed", "graph", "evidence", "voice", "ingest", "settings"].map((name) => (
          <Route key={name} path={`/${name}`} element={<LegacyRedirect to={`/app/${name}`} />} />
        ))}
        <Route path="*" element={<Navigate to="/app/feed" replace />} />
      </Routes>
    </Suspense>
  );
}
