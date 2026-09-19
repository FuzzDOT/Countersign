import { useCallback, useEffect, useState } from 'react';

/**
 * A live countdown for controls that are disabled by RATE_LIMITED. The number
 * is shown on the button itself (brief §4.3). Ticks four times a second so the
 * displayed second never lags.
 */
export function useCooldown() {
  const [until, setUntil] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (until === null) return undefined;
    setNow(Date.now());
    const timer = window.setInterval(() => {
      const t = Date.now();
      setNow(t);
      if (t >= until) setUntil(null);
    }, 250);
    return () => window.clearInterval(timer);
  }, [until]);

  const start = useCallback((seconds: number) => setUntil(Date.now() + seconds * 1000), []);
  const clear = useCallback(() => setUntil(null), []);
  const remaining = until === null ? 0 : Math.max(0, Math.ceil((until - now) / 1000));

  return { remaining, active: remaining > 0, start, clear };
}
