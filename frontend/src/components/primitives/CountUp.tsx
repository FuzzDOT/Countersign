import { useEffect, useRef, useState } from "react";

type CountUpProps = {
  from: number;
  to: number;
  durationMs?: number;
  decimals?: number;
  suffix?: string;
};

export function CountUp({ from, to, durationMs = 600, decimals = 0, suffix = "" }: CountUpProps) {
  const [value, setValue] = useState(from);
  const rafRef = useRef<number>();

  useEffect(() => {
    const prefersReduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (prefersReduced) {
      setValue(to);
      return;
    }

    const start = performance.now();
    const tick = (now: number) => {
      const progress = Math.min(1, (now - start) / durationMs);
      // ease-out cubic, matches --ease-out's feel closely enough without importing the exact bezier
      const eased = 1 - Math.pow(1 - progress, 3);
      setValue(from + (to - from) * eased);
      if (progress < 1) rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);

    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    };
  }, [from, to, durationMs]);

  return (
    <span className="font-mono tabular-nums">
      {value.toFixed(decimals)}
      {suffix}
    </span>
  );
}