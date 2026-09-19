import { useInsightStats } from "../api/queries";

/** Escalated count for the rail badge. Reads the shared, authenticated query so it shares the feed's cache. */
export function useFeedStats(): number | undefined {
  const { data } = useInsightStats();
  return data?.by_routing.escalate_now;
}
