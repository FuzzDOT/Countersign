import { describe, expect, it } from 'vitest';
import { arcGeometry, bezierLength } from './arcs';

describe('bezierLength', () => {
  it('matches a straight line when the control point is on it', () => {
    expect(bezierLength(0, 0, 50, 0, 100, 0)).toBeCloseTo(100, 5);
  });

  it('is longer than the chord when the curve bows', () => {
    expect(bezierLength(0, 0, 50, -100, 100, 0)).toBeGreaterThan(100);
  });
});

describe('arcGeometry', () => {
  it('arches higher for longer links, up to the cap', () => {
    const short = arcGeometry(0, 40, 100, 90);
    const long = arcGeometry(0, 200, 100, 90);
    const huge = arcGeometry(0, 5000, 100, 90);
    expect(long.apexY).toBeLessThan(short.apexY);
    expect(huge.apexY).toBe(100 - 90);
  });

  it('reaches exactly the visible apex, because a quadratic only gets half way to its control point', () => {
    const arc = arcGeometry(10, 110, 100, 90);
    const height = 14 + 100 * 0.28;
    expect(arc.apexY).toBeCloseTo(100 - height, 5);
    expect(arc.d).toBe(`M 10 100 Q 60 ${100 - 2 * height} 110 100`);
  });

  it('is symmetric in direction', () => {
    expect(arcGeometry(20, 120, 50, 60).length).toBeCloseTo(arcGeometry(120, 20, 50, 60).length, 5);
  });
});
