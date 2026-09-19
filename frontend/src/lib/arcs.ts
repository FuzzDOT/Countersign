export interface ArcGeometry {
  /** SVG path data for a quadratic Bézier from token to token. */
  d: string;
  /** Midpoint x and apex y, for a weight label. */
  midX: number;
  apexY: number;
  /** Approximate length, used to draw the "break" with stroke-dashoffset. */
  length: number;
}

/**
 * A quadratic Bézier arch between two token centres. Longer links arch higher
 * (capped) so overlapping arcs stay distinguishable. The control point sits at
 * twice the visible height because a quadratic curve only reaches half-way to
 * its control point.
 */
export function arcGeometry(x1: number, x2: number, baseY: number, maxHeight: number): ArcGeometry {
  const dx = Math.abs(x2 - x1);
  const height = Math.min(maxHeight, 14 + dx * 0.28);
  const midX = (x1 + x2) / 2;
  const controlY = baseY - 2 * height;
  return {
    d: `M ${x1} ${baseY} Q ${midX} ${controlY} ${x2} ${baseY}`,
    midX,
    apexY: baseY - height,
    length: bezierLength(x1, baseY, midX, controlY, x2, baseY),
  };
}

/** Polyline approximation of a quadratic Bézier's arc length. Pure, so it needs no DOM. */
export function bezierLength(
  x0: number,
  y0: number,
  cx: number,
  cy: number,
  x1: number,
  y1: number,
  steps = 24,
): number {
  let length = 0;
  let px = x0;
  let py = y0;
  for (let i = 1; i <= steps; i += 1) {
    const t = i / steps;
    const u = 1 - t;
    const x = u * u * x0 + 2 * u * t * cx + t * t * x1;
    const y = u * u * y0 + 2 * u * t * cy + t * t * y1;
    length += Math.hypot(x - px, y - py);
    px = x;
    py = y;
  }
  return length;
}
