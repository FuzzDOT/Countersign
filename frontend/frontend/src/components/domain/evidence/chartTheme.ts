/** Shared Recharts styling. Axis and grid use ink tokens; colour is reserved for routing severity. */
export const AXIS_TICK = { fill: 'var(--ink-200)', fontSize: 11 } as const;
export const AXIS_LINE = 'var(--ink-500)';
export const GRID_STROKE = 'var(--ink-500)';

export function axisLabel(value: string, position: 'insideBottom' | 'insideLeft', extra: Record<string, unknown> = {}) {
  return {
    value,
    position,
    fill: 'var(--ink-200)',
    fontSize: 12,
    ...extra,
  };
}

export const percentTick = (v: number) => `${Math.round(v * 100)}%`;
