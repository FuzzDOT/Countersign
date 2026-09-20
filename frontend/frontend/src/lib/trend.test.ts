import { describe, expect, it } from 'vitest';
import { fitLine } from './trend';

describe('fitLine', () => {
  it('recovers an exact line', () => {
    const line = fitLine([{ x: 0, y: 1 }, { x: 1, y: 3 }, { x: 2, y: 5 }]);
    expect(line?.slope).toBeCloseTo(2, 8);
    expect(line?.intercept).toBeCloseTo(1, 8);
  });

  it('returns null when there is nothing to fit', () => {
    expect(fitLine([])).toBeNull();
    expect(fitLine([{ x: 1, y: 1 }])).toBeNull();
    expect(fitLine([{ x: 1, y: 1 }, { x: 1, y: 5 }])).toBeNull();
  });

  it('ignores non-finite points', () => {
    const line = fitLine([{ x: 0, y: 0 }, { x: Number.NaN, y: 4 }, { x: 1, y: 1 }]);
    expect(line?.slope).toBeCloseTo(1, 8);
  });
});
