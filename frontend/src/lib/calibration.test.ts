import { describe, expect, it } from 'vitest';
import { improvementSentence, relativeChange } from './calibration';

describe('improvementSentence', () => {
  it('states the improvement in words, from the numbers', () => {
    expect(improvementSentence(-0.56, 43)).toBe(
      'Calibration error fell 56% against 43 logged hard cases.',
    );
  });

  it('uses the singular for one case', () => {
    expect(improvementSentence(-0.2, 1)).toBe(
      'Calibration error fell 20% against 1 logged hard case.',
    );
  });

  it('is honest when calibration got worse or did not move', () => {
    expect(improvementSentence(0.1, 10)).toBe(
      'Calibration error rose 10% against 10 logged hard cases.',
    );
    expect(improvementSentence(0, 10)).toBe(
      'Calibration error did not change against 10 logged hard cases.',
    );
  });
});

describe('relativeChange', () => {
  it('is negative when error falls', () => {
    expect(relativeChange(0.1, 0.044)).toBeCloseTo(-0.56, 5);
  });
  it('does not divide by zero', () => {
    expect(relativeChange(0, 0.2)).toBe(0);
  });
});
