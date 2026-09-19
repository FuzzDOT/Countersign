import { useId } from 'react';

interface SingleProps {
  label: string;
  min?: number;
  max?: number;
  step?: number;
  value: number;
  onChange: (value: number) => void;
  format?: (value: number) => string;
}

interface DualProps {
  label: string;
  min?: number;
  max?: number;
  step?: number;
  value: readonly [number, number];
  onChange: (value: [number, number]) => void;
  format?: (value: number) => string;
}

const defaultFormat = (v: number) => v.toFixed(2);

/** Single-handle slider. Native <input type="range"> underneath, so keyboard and AT come free. */
export function RangeSlider({
  label,
  min = 0,
  max = 1,
  step = 0.01,
  value,
  onChange,
  format = defaultFormat,
}: SingleProps) {
  const id = useId();
  const pct = ((value - min) / (max - min)) * 100;
  return (
    <div>
      <div className="mb-1 flex items-baseline justify-between text-body-sm">
        <label htmlFor={id} className="font-medium text-ink-50">
          {label}
        </label>
        <span className="nums text-ink-200">{format(value)}</span>
      </div>
      <div className="range">
        <div className="range__track" />
        <div className="range__fill" style={{ left: 0, width: `${pct}%` }} />
        <input
          id={id}
          type="range"
          min={min}
          max={max}
          step={step}
          value={value}
          onChange={(event) => onChange(Number(event.target.value))}
        />
      </div>
    </div>
  );
}

/** Dual-handle slider. The handles cannot cross. */
export function DualRangeSlider({
  label,
  min = 0,
  max = 1,
  step = 0.01,
  value,
  onChange,
  format = defaultFormat,
}: DualProps) {
  const [lo, hi] = value;
  const span = max - min;
  const loPct = ((lo - min) / span) * 100;
  const hiPct = ((hi - min) / span) * 100;
  return (
    <div role="group" aria-label={label}>
      <div className="mb-1 flex items-baseline justify-between text-body-sm">
        <span className="font-medium text-ink-50">{label}</span>
        <span className="nums text-ink-200">
          {format(lo)} to {format(hi)}
        </span>
      </div>
      <div className="range">
        <div className="range__track" />
        <div className="range__fill" style={{ left: `${loPct}%`, width: `${hiPct - loPct}%` }} />
        <input
          type="range"
          aria-label={`${label}, minimum`}
          min={min}
          max={max}
          step={step}
          value={lo}
          // When both handles sit at the top, the lower one must stay grabbable.
          style={{ zIndex: lo > min + span / 2 ? 5 : 3 }}
          onChange={(event) => onChange([Math.min(Number(event.target.value), hi), hi])}
        />
        <input
          type="range"
          aria-label={`${label}, maximum`}
          min={min}
          max={max}
          step={step}
          value={hi}
          style={{ zIndex: 4 }}
          onChange={(event) => onChange([lo, Math.max(Number(event.target.value), lo)])}
        />
      </div>
    </div>
  );
}
