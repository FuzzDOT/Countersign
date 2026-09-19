<<<<<<< HEAD
import { lazy, Suspense } from 'react';
import { Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { RequireAuth } from './auth/RequireAuth';
import { AppShell } from './components/shell/AppShell';
import { Skeleton } from './components/primitives/Skeleton';
import IngestPage from './routes/ingest/IngestPage';
import SettingsPage from './routes/settings/SettingsPage';
import DevLogin from './routes/dev1-placeholders/DevLogin';
=======
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
>>>>>>> 04ef22a88f7d0a831b4ff1c0a31ca02ef46387d5

// Frontend dev 2 screens are code-split so d3 (graph chunk) and Recharts
// (evidence chunk) stay out of the landing bundle (brief §16).
const FeedRoute = lazy(() => import('./routes/app/FeedRoute'));
const GraphRoute = lazy(() => import('./routes/app/GraphRoute'));
const DocumentRoute = lazy(() => import('./routes/app/DocumentRoute'));
const EvidenceRoute = lazy(() => import('./routes/app/EvidenceRoute'));
const VoiceRoute = lazy(() => import('./routes/app/VoiceRoute'));

/** Old shell paths (/feed) forward to the brief's route map (/app/feed), keeping the query string. */
function LegacyRedirect({ to }: { to: string }) {
  const { search } = useLocation();
  return <Navigate to={`${to}${search}`} replace />;
}

/**
 * Route map (brief §3). DEV 1: `/` becomes the landing page and /login the real
 * sign-in; DevLogin is a bare stand-in so the app can authenticate in development.
 */
export default function App() {
  return (
<<<<<<< HEAD
    <Suspense fallback={<Skeleton className="m-6 h-10 w-64" />}>
      <Routes>
        <Route path="/" element={<Navigate to="/app/feed" replace />} />
        <Route path="/login" element={<DevLogin />} />
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
        {['feed', 'graph', 'evidence', 'voice', 'ingest', 'settings'].map((name) => (
          <Route key={name} path={`/${name}`} element={<LegacyRedirect to={`/app/${name}`} />} />
        ))}
        <Route path="*" element={<Navigate to="/app/feed" replace />} />
      </Routes>
    </Suspense>
  );
}
=======
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
>>>>>>> 04ef22a88f7d0a831b4ff1c0a31ca02ef46387d5
