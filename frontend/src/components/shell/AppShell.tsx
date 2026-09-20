import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Outlet, useNavigate } from "react-router-dom";
import { prefetchVoiceFallback } from "../../api/queries";
import { RailNav } from "./RailNav";
import { TopBar } from "./TopBar";
import { RouteTransition } from "./RouteTransition";
import { useFeedStats } from "../../hooks/useFeedStats";
import { useAuth } from "@/auth/useAuth";
// import { useIngestJobSocket } from "../../hooks/useIngestJobSocket"; // build once ws setup is confirmed

export function AppShell() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  // Get the recorded briefing (JSON + audio) into cache before it is needed (brief §12.3, §16).
  useEffect(() => {
    void prefetchVoiceFallback(queryClient);
  }, [queryClient]);
  const escalateCount = useFeedStats();
  const { me } = useAuth();
  const jobProgress = null; // wire to websocket hook once that's built

  return (
    <div className="flex h-screen">
      <RailNav escalateCount={escalateCount} />
      <div className="flex flex-col flex-1 min-w-0">
        <TopBar
          orgName={me?.org_name ?? "—"}
          scenarioName="—"
          jobProgress={jobProgress}
          onSearch={(q) => navigate(`/app/feed?q=${encodeURIComponent(q)}`)}
        />
        {/* Ink surface (brief §2.1): only the document reader flips to paper, and it does so itself. Screens manage their own scrolling. */}
        <main className="relative min-h-0 flex-1 overflow-hidden bg-ink-900 text-ink-50">
          <RouteTransition>
            <Outlet />
          </RouteTransition>
        </main>
      </div>
    </div>
  );
}