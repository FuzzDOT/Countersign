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