<<<<<<< HEAD
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
=======
type MeterProps = {
  label: string;
  value: number; // 0–1
  formattedValue?: string; // e.g. "0.81" — falls back to percentage
};

export function Meter({ label, value, formattedValue }: MeterProps) {
  const clamped = Math.min(1, Math.max(0, value));

  return (
    <div className="flex items-center gap-2">
      <span className="text-body-sm text-ink-200 w-16">{label}</span>
      <div className="w-[120px] h-1 bg-ink-500/40 rounded-full overflow-hidden" role="meter" aria-valuenow={clamped} aria-valuemin={0} aria-valuemax={1} aria-label={label}>
        <div className="h-full bg-ink-50" style={{ width: `${clamped * 100}%` }} />
      </div>
      <span className="text-body-sm text-ink-50 font-mono w-10 text-right">
        {formattedValue ?? clamped.toFixed(2)}
      </span>
    </div>
  );
}
>>>>>>> 04ef22a88f7d0a831b4ff1c0a31ca02ef46387d5
