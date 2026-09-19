<<<<<<< HEAD
import { useCountUp } from '@/hooks/useCountUp';

interface CountUpProps {
  value: number;
  format?: (value: number) => string;
  duration?: number;
  from?: number;
  replayKey?: string | number | null;
  className?: string;
}

const defaultFormat = (n: number) => Math.round(n).toString();

/**
 * The single animated number (brief §14). The moving digits are hidden from
 * assistive tech and the settled value is provided as text, so a screen reader
 * hears the answer once instead of forty intermediate values.
 */
export function CountUp({
  value,
  format = defaultFormat,
  duration,
  from,
  replayKey,
  className,
}: CountUpProps) {
  const options: { duration?: number; from?: number; replayKey?: string | number | null } = {};
  if (duration !== undefined) options.duration = duration;
  if (from !== undefined) options.from = from;
  if (replayKey !== undefined) options.replayKey = replayKey;
  const current = useCountUp(value, options);
  return (
    <>
      <span aria-hidden="true" className={`nums ${className ?? ''}`}>
        {format(current)}
      </span>
      <span className="sr-only">{format(value)}</span>
    </>
  );
}
=======
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
>>>>>>> 04ef22a88f7d0a831b4ff1c0a31ca02ef46387d5
