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
