/** Recharts hands click handlers the point in slightly different shapes across versions; find the id wherever it is. */
export function insightIdFromChartClick(input: unknown): string | null {
  const seen = new Set<unknown>();
  const visit = (value: unknown, depth: number): string | null => {
    if (typeof value !== 'object' || value === null || depth > 3 || seen.has(value)) return null;
    seen.add(value);
    const record = value as Record<string, unknown>;
    if (typeof record['id'] === 'string') return record['id'];
    for (const key of ['payload', 'node']) {
      const found = visit(record[key], depth + 1);
      if (found) return found;
    }
    return null;
  };
  return visit(input, 0);
}
