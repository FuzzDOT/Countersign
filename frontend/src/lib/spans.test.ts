import { describe, expect, it } from 'vitest';
import { buildSegments, createOffsetMapper, firstSegmentIndex, normalizeRange } from './spans';

const TEXT =
  'Invoice 4471: payment of $48,200 was routed through Advent Holdings on behalf of Meridian Supply LLC.';

const at = (needle: string) => {
  const start = TEXT.indexOf(needle);
  return { start, end: start + needle.length };
};

describe('buildSegments', () => {
  it('reassembles raw_text exactly, byte for byte, with no trimming or normalising', () => {
    const messy = '  Line one\r\n\tLine  two   \n\n';
    const segments = buildSegments(messy, [{ insight_id: 'a', char_start: 2, char_end: 10 }]);
    expect(segments.map((s) => s.text).join('')).toBe(messy);
  });

  it('marks a single span and leaves the rest plain', () => {
    const { start, end } = at('was routed through Advent Holdings');
    const segments = buildSegments(TEXT, [{ insight_id: 'a', char_start: start, char_end: end }]);
    const marked = segments.filter((s) => s.spanIds.length > 0);
    expect(marked).toHaveLength(1);
    expect(marked[0]?.text).toBe('was routed through Advent Holdings');
    expect(segments.map((s) => s.text).join('')).toBe(TEXT);
  });

  it('splits overlapping spans at every boundary without losing or duplicating text', () => {
    // A: "payment of $48,200 was routed through Advent Holdings"
    // B: "was routed through Advent Holdings on behalf of Meridian Supply LLC"
    const a = { start: TEXT.indexOf('payment'), end: TEXT.indexOf(' on behalf') };
    const b = { start: TEXT.indexOf('was routed'), end: TEXT.indexOf('.', TEXT.indexOf('LLC')) };
    const segments = buildSegments(TEXT, [
      { insight_id: 'A', char_start: a.start, char_end: a.end },
      { insight_id: 'B', char_start: b.start, char_end: b.end },
    ]);

    expect(segments.map((s) => s.text).join('')).toBe(TEXT);

    const onlyA = segments.filter((s) => s.spanIds.join() === 'A');
    const both = segments.filter((s) => s.spanIds.length === 2);
    const onlyB = segments.filter((s) => s.spanIds.join() === 'B');
    expect(onlyA.map((s) => s.text).join('')).toBe('payment of $48,200 ');
    expect(both.map((s) => s.text).join('')).toBe('was routed through Advent Holdings');
    expect(onlyB.map((s) => s.text).join('')).toBe(' on behalf of Meridian Supply LLC');
  });

  it('handles a span nested inside another, and identical spans', () => {
    const outer = at('routed through Advent Holdings on behalf of');
    const inner = at('Advent Holdings');
    const segments = buildSegments(TEXT, [
      { insight_id: 'outer', char_start: outer.start, char_end: outer.end },
      { insight_id: 'inner', char_start: inner.start, char_end: inner.end },
      { insight_id: 'twin', char_start: inner.start, char_end: inner.end },
    ]);
    expect(segments.map((s) => s.text).join('')).toBe(TEXT);
    const core = segments.find((s) => s.text === 'Advent Holdings');
    expect(core?.spanIds.sort()).toEqual(['inner', 'outer', 'twin']);
  });

  it('handles spans that touch end to end', () => {
    const segments = buildSegments('abcdef', [
      { insight_id: 'x', char_start: 0, char_end: 3 },
      { insight_id: 'y', char_start: 3, char_end: 6 },
    ]);
    expect(segments.map((s) => [s.text, s.spanIds])).toEqual([
      ['abc', ['x']],
      ['def', ['y']],
    ]);
  });

  it('does not crash on malformed offsets: they produce a missing highlight', () => {
    const bad = [
      { insight_id: 'neg', char_start: -5, char_end: 4 },
      { insight_id: 'past', char_start: 90, char_end: 9999 },
      { insight_id: 'inverted', char_start: 20, char_end: 10 },
      { insight_id: 'empty', char_start: 7, char_end: 7 },
      { insight_id: 'nan', char_start: Number.NaN, char_end: 12 },
      { insight_id: 'inf', char_start: 0, char_end: Number.POSITIVE_INFINITY },
    ];
    const segments = buildSegments('0123456789'.repeat(10), bad);
    expect(segments.map((s) => s.text).join('')).toBe('0123456789'.repeat(10));
    const ids = new Set(segments.flatMap((s) => s.spanIds));
    expect(ids.has('inverted')).toBe(false);
    expect(ids.has('empty')).toBe(false);
    expect(ids.has('nan')).toBe(false);
    expect(ids.has('inf')).toBe(false);
    // Clamped, not dropped: a range that partly overlaps the text still marks the overlap.
    expect(ids.has('neg')).toBe(true);
    expect(ids.has('past')).toBe(true);
  });

  it('returns nothing for empty text', () => {
    expect(buildSegments('', [{ insight_id: 'a', char_start: 0, char_end: 3 }])).toEqual([]);
  });

  it('tracks entity mentions separately from citation spans', () => {
    const name = at('Advent Holdings');
    const segments = buildSegments(
      TEXT,
      [{ insight_id: 'a', char_start: 0, char_end: TEXT.length }],
      [{ char_start: name.start, char_end: name.end }],
    );
    const mention = segments.find((s) => s.mentionIdx.length > 0);
    expect(mention?.text).toBe('Advent Holdings');
    expect(mention?.spanIds).toEqual(['a']);
    expect(segments.map((s) => s.text).join('')).toBe(TEXT);
  });

  it('finds the first segment a span touches', () => {
    const { start, end } = at('Meridian Supply LLC');
    const segments = buildSegments(TEXT, [{ insight_id: 'm', char_start: start, char_end: end }]);
    const index = firstSegmentIndex(segments, 'm');
    expect(segments[index]?.text).toBe('Meridian Supply LLC');
    expect(firstSegmentIndex(segments, 'missing')).toBe(-1);
  });

  it('keeps text that looks like markup as inert text', () => {
    const hostile = 'Pay <img src=x onerror=alert(1)> now & **never** [link](javascript:x)';
    const segments = buildSegments(hostile, [{ insight_id: 'a', char_start: 4, char_end: 30 }]);
    expect(segments.map((s) => s.text).join('')).toBe(hostile);
  });
});

describe('offsets from a code-point indexed backend', () => {
  it('is the identity for BMP-only text', () => {
    const map = createOffsetMapper('plain ascii text');
    expect(map(0)).toBe(0);
    expect(map(7)).toBe(7);
  });

  it('shifts past characters outside the BMP so highlights stay aligned', () => {
    const text = 'a\u{1F600}bc'; // the emoji is one code point but two UTF-16 units
    const map = createOffsetMapper(text);
    expect(map(1)).toBe(1);
    expect(map(2)).toBe(3);
    // The backend says "b" is code point 2..3; in UTF-16 that is 3..4.
    const segments = buildSegments(text, [{ insight_id: 'b', char_start: 2, char_end: 3 }]);
    expect(segments.find((s) => s.spanIds.length > 0)?.text).toBe('b');
  });
});

describe('normalizeRange', () => {
  it('clamps into the text and rejects empty results', () => {
    expect(normalizeRange(-3, 5, 10)).toEqual([0, 5]);
    expect(normalizeRange(4, 99, 10)).toEqual([4, 10]);
    expect(normalizeRange(5, 5, 10)).toBeNull();
    expect(normalizeRange(8, 2, 10)).toBeNull();
  });
});
