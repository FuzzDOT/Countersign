import { useState, useEffect } from "react";

export function useFeedStats() {
  const [escalateCount, setEscalateCount] = useState<number | undefined>();

  useEffect(() => {
    let alive = true;
    fetch(`${import.meta.env.VITE_API_BASE_URL}/insights/stats`)
      .then((r) => r.json())
      .then((data) => {
        if (alive) setEscalateCount(data?.by_routing?.escalate_now);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);

  return escalateCount;
}