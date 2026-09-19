import { Outlet, useNavigate } from "react-router-dom";
import { RailNav } from "./RailNav";
import { TopBar } from "./TopBar";
import { RouteTransition } from "./RouteTransition";
import { useFeedStats } from "../../hooks/useFeedStats";
// import { useIngestJobSocket } from "../../hooks/useIngestJobSocket"; // build once ws setup is confirmed

export function AppShell() {
  const navigate = useNavigate();
  const escalateCount = useFeedStats();
  const jobProgress = null; // wire to websocket hook once that's built

  return (
    <div className="flex h-screen">
      <RailNav escalateCount={escalateCount} />
      <div className="flex flex-col flex-1 min-w-0">
        <TopBar
          orgName="Countersign"
          scenarioName="—"
          jobProgress={jobProgress}
          onSearch={(q) => navigate(`/insights?q=${encodeURIComponent(q)}`)}
        />
        <main className="flex-1 overflow-auto bg-[var(--paper)] text-[var(--paper-text)]">
          <RouteTransition>
            <Outlet />
          </RouteTransition>
        </main>
      </div>
    </div>
  );
}