import { unit } from '@/lib/format';
import { cn } from '@/lib/cn';

interface MeterProps {
  /** 0..1. Callers feed it a CountUp value when the number animates. */
  value: number;
  label: string;
  className?: string | undefined;
  /** Track width in px. The feed uses 120. */
  width?: number | undefined;
}

/**
 * Confidence and vacuity are encoded in position, length and number, never in
 * colour (colour is spent on severity). So: ink-050 fill on an ink-500 track.
 */
export function Meter({ value, label, className, width = 120 }: MeterProps) {
  const clamped = unit(value);
  return (
    <div
      role="meter"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={1}
      aria-valuenow={Number(clamped.toFixed(2))}
      className={cn('h-1 shrink-0 overflow-hidden rounded-input bg-ink-500', className)}
      style={{ width }}
    >
      <div className="h-full bg-ink-50" style={{ width: `${clamped * 100}%` }} />
    </div>
  );
}
