/**
 * Turns `raw_text` plus citation spans and entity mentions into an ordered
 * list of segments to render.
 *
 * The rules that make this safe (brief §10.2, §18):
 *  - `raw_text` is sliced, never edited: no trim, no whitespace normalising.
 *    Every offset in the system indexes into this exact string.
 *  - Overlapping spans are split at every boundary. Two insights citing
 *    overlapping ranges is realistic and must not crash or drop text.
 *  - A malformed offset produces a missing highlight, never an exception.
 *  - Output is plain strings. The caller renders them as text nodes.
 */

export interface SpanLike {
  insight_id: string;
  char_start: number;
  char_end: number;
}

export interface MentionLike {
  char_start: number;
  char_end: number;
}

export interface Segment {
  /** UTF-16 offsets into the original string. */
  start: number;
  end: number;
  text: string;
  /** insight_ids of every span covering this segment, ordered by start then longest first. */
  spanIds: string[];
  /** Indexes into the `mentions` array passed to buildSegments. */
  mentionIdx: number[];
}

/**
 * The API reports offsets as Python string indices (code points); JS strings
 * index by UTF-16 code unit. They only differ for characters outside the BMP.
 * The synthetic corpus is BMP-only, so this is identity in practice, but a
 * pasted emoji must shift highlights by zero rather than by one.
 */
export function createOffsetMapper(text: string): (codePointIndex: number) => number {
  let hasSurrogates = false;
  for (let i = 0; i < text.length; i += 1) {
    const code = text.charCodeAt(i);
    if (code >= 0xd800 && code <= 0xdbff) {
      hasSurrogates = true;
      break;
    }
  }
  if (!hasSurrogates) return (index) => index;

  const table: number[] = [0];
  let unit = 0;
  for (const char of text) {
    unit += char.length;
    table.push(unit);
  }
  return (index) => {
    if (index <= 0) return 0;
    const mapped = table[Math.trunc(index)];
    return mapped ?? text.length;
  };
}

/** Clamp a range into [0, length]. Returns null when nothing valid is left. */
export function normalizeRange(
  start: number,
  end: number,
  length: number,
): [number, number] | null {
  if (!Number.isFinite(start) || !Number.isFinite(end)) return null;
  const s = Math.min(length, Math.max(0, Math.trunc(start)));
  const e = Math.min(length, Math.max(0, Math.trunc(end)));
  return e > s ? [s, e] : null;
}

interface Range {
  id: string;
  index: number;
  start: number;
  end: number;
}

export function buildSegments(
  text: string,
  spans: readonly SpanLike[],
  mentions: readonly MentionLike[] = [],
): Segment[] {
  if (text.length === 0) return [];
  const toUnits = createOffsetMapper(text);

  const spanRanges: Range[] = [];
  spans.forEach((span, index) => {
    const range = normalizeRange(toUnits(span.char_start), toUnits(span.char_end), text.length);
    if (range) spanRanges.push({ id: span.insight_id, index, start: range[0], end: range[1] });
  });
  spanRanges.sort((a, b) => a.start - b.start || b.end - a.end);

  const mentionRanges: Range[] = [];
  mentions.forEach((mention, index) => {
    const range = normalizeRange(
      toUnits(mention.char_start),
      toUnits(mention.char_end),
      text.length,
    );
    if (range) mentionRanges.push({ id: String(index), index, start: range[0], end: range[1] });
  });

  const boundarySet = new Set<number>([0, text.length]);
  for (const r of spanRanges) {
    boundarySet.add(r.start);
    boundarySet.add(r.end);
  }
  for (const r of mentionRanges) {
    boundarySet.add(r.start);
    boundarySet.add(r.end);
  }
  const boundaries = Array.from(boundarySet).sort((a, b) => a - b);

  const segments: Segment[] = [];
  for (let i = 0; i < boundaries.length - 1; i += 1) {
    const start = boundaries[i];
    const end = boundaries[i + 1];
    if (start === undefined || end === undefined || end <= start) continue;

    const spanIds = spanRanges.filter((r) => r.start <= start && r.end >= end).map((r) => r.id);
    const mentionIdx = mentionRanges
      .filter((r) => r.start <= start && r.end >= end)
      .map((r) => r.index);

    const previous = segments[segments.length - 1];
    if (previous && sameList(previous.spanIds, spanIds) && sameList(previous.mentionIdx, mentionIdx)) {
      previous.end = end;
      previous.text = text.slice(previous.start, end);
      continue;
    }
    segments.push({ start, end, text: text.slice(start, end), spanIds, mentionIdx });
  }
  return segments;
}

function sameList<T>(a: readonly T[], b: readonly T[]): boolean {
  return a.length === b.length && a.every((value, index) => value === b[index]);
}

/** Index of the first segment that a given span touches, or -1. */
export function firstSegmentIndex(segments: readonly Segment[], spanId: string): number {
  return segments.findIndex((segment) => segment.spanIds.includes(spanId));
}
