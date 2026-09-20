import { describe, expect, it } from 'vitest';
import { EMPTY_FILTERS, activeFilterCount, parseFeedFilters, toApiQuery, writeFeedFilters } from './feedFilters';

describe('feed filters in the URL', () => {
  it('parses an empty query string to the defaults', () => {
    expect(parseFeedFilters(new URLSearchParams(''))).toEqual(EMPTY_FILTERS);
    expect(activeFilterCount(EMPTY_FILTERS)).toBe(0);
  });

  it('round-trips through the URL', () => {
    const filters = {
      ...EMPTY_FILTERS,
      routing: ['escalate_now', 'flag_for_review'] as const,
      resolvedBy: 'nemotron' as const,
      minConfidence: 0.5,
      maxConfidence: 0.9,
      minVacuity: 0.2,
      relation: ['OWNED_BY'],
      sort: '-vacuity' as const,
      q: 'advent holdings',
    };
    const written = writeFeedFilters(new URLSearchParams(), { ...filters, routing: [...filters.routing] });
    expect(parseFeedFilters(written)).toEqual({ ...filters, routing: [...filters.routing] });
    expect(written.getAll('routing')).toEqual(['escalate_now', 'flag_for_review']);
  });

  it('drops junk values instead of trusting them', () => {
    const parsed = parseFeedFilters(new URLSearchParams('routing=bogus&sort=nope&min_confidence=abc&resolved_by=alien'));
    expect(parsed.routing).toEqual([]);
    expect(parsed.sort).toBe(EMPTY_FILTERS.sort);
    expect(parsed.minConfidence).toBeNull();
    expect(parsed.resolvedBy).toBeNull();
  });

  it('keeps unrelated params such as the open sheet', () => {
    const written = writeFeedFilters(new URLSearchParams('insight=abc'), { ...EMPTY_FILTERS, q: 'x' });
    expect(written.get('insight')).toBe('abc');
    expect(written.get('q')).toBe('x');
  });

  it('maps to API query params', () => {
    const query = toApiQuery({ ...EMPTY_FILTERS, routing: ['auto_file'], q: 'x' });
    expect(query['routing']).toEqual(['auto_file']);
    expect(query['q']).toBe('x');
  });
});
