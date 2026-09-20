import { Meter } from '@/components/primitives/Meter';
import { formatScore } from '@/lib/format';
import { cn } from '@/lib/cn';

interface ScoreMeterProps {
  value: number;
  className?: string | undefined;
  /** Show the word beside the number. The feed row does; a table cell does not. */
  showLabel?: boolean | undefined;
  width?: number | undefined;
}

function ScoreMeter({
  value,
  label,
  className,
  showLabel = true,
  width,
}: ScoreMeterProps & { label: string }) {
  return (
    <div className={cn('flex items-center justify-end gap-2', className)}>
      {showLabel ? (
        <span className="w-16 text-right text-body-sm text-ink-200">{label}</span>
      ) : null}
      <span className="nums w-9 text-right text-body-sm text-ink-50">{formatScore(value)}</span>
      <Meter value={value} label={label} width={width ?? 120} />
    </div>
  );
}

export function ConfidenceMeter(props: ScoreMeterProps) {
  return <ScoreMeter {...props} label="confidence" />;
}

export function VacuityMeter(props: ScoreMeterProps) {
  return <ScoreMeter {...props} label="vacuity" />;
}
