export interface Point {
  x: number;
  y: number;
}

export interface Line {
  slope: number;
  intercept: number;
}

/**
 * Ordinary least squares for the fragility scatter's fitted trend line.
 * Returns null when there is nothing to fit (fewer than two points, or every
 * x identical), so the caller can simply omit the line.
 */
export function fitLine(points: readonly Point[]): Line | null {
  const usable = points.filter((p) => Number.isFinite(p.x) && Number.isFinite(p.y));
  const n = usable.length;
  if (n < 2) return null;
  let sx = 0;
  let sy = 0;
  for (const p of usable) {
    sx += p.x;
    sy += p.y;
  }
  const mx = sx / n;
  const my = sy / n;
  let num = 0;
  let den = 0;
  for (const p of usable) {
    num += (p.x - mx) * (p.y - my);
    den += (p.x - mx) ** 2;
  }
  if (den === 0) return null;
  const slope = num / den;
  return { slope, intercept: my - slope * mx };
}
