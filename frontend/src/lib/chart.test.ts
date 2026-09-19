import { describe, expect, it } from 'vitest';
import { insightIdFromChartClick } from './chart';

describe('insightIdFromChartClick', () => {
  it('finds the id on the point itself, in payload, or in node', () => {
    expect(insightIdFromChartClick({ id: 'a' })).toBe('a');
    expect(insightIdFromChartClick({ payload: { id: 'b' } })).toBe('b');
    expect(insightIdFromChartClick({ node: { id: 'c' } })).toBe('c');
  });
  it('returns null for anything else, without throwing', () => {
    expect(insightIdFromChartClick(null)).toBeNull();
    expect(insightIdFromChartClick('x')).toBeNull();
    expect(insightIdFromChartClick({ payload: 5 })).toBeNull();
  });
});
