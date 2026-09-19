/** "Calibration error fell 56% against 43 logged hard cases." Built from numbers, not generated. */
export function improvementSentence(eceRelative: number, hardCases: number): string {
  const pct = Math.round(Math.abs(eceRelative) * 100);
  const cases = `${hardCases} logged hard ${hardCases === 1 ? 'case' : 'cases'}`;
  if (eceRelative < 0) return `Calibration error fell ${pct}% against ${cases}.`;
  if (eceRelative > 0) return `Calibration error rose ${pct}% against ${cases}.`;
  return `Calibration error did not change against ${cases}.`;
}

/** Relative change in ECE between two snapshots, negative meaning better. */
export function relativeChange(before: number, after: number): number {
  return before === 0 ? 0 : (after - before) / before;
}
