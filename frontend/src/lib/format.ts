/** Formatting helpers. Pure and locale-pinned so tests and screens agree. */

export function formatScore(value: number): string {
  return value.toFixed(2);
}

export function formatPercent(value: number, digits = 0): string {
  return `${(value * 100).toFixed(digits)}%`;
}

export function formatSigned(value: number, digits = 2): string {
  const text = Math.abs(value).toFixed(digits);
  if (value > 0) return `+${text}`;
  if (value < 0) return `\u2212${text}`; // a real minus sign
  return text;
}

const shortDate = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short' });
const longDate = new Intl.DateTimeFormat('en-GB', {
  day: 'numeric',
  month: 'short',
  year: 'numeric',
});
const dateTime = new Intl.DateTimeFormat('en-GB', {
  day: 'numeric',
  month: 'short',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
});

function parse(iso: string): Date | null {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function formatDateShort(iso: string): string {
  const date = parse(iso);
  return date ? shortDate.format(date) : iso;
}

export function formatDateLong(iso: string): string {
  const date = parse(iso);
  return date ? longDate.format(date) : iso;
}

export function formatDateTime(iso: string): string {
  const date = parse(iso);
  return date ? dateTime.format(date) : iso;
}

export function formatMs(ms: number): string {
  if (ms >= 1000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.round(ms)}ms`;
}

/** m:ss for the audio transport. */
export function formatClock(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return `${minutes}:${seconds.toString().padStart(2, '0')}`;
}

export function formatPValue(p: number): string {
  if (p < 0.001) return 'p < 0.001';
  return `p = ${p.toFixed(3)}`;
}

export function formatCount(value: number): string {
  return Math.round(value).toLocaleString('en-GB');
}

/** First segment of a UUID, for places where the full id is noise. */
export function shortId(id: string): string {
  return id.slice(0, 8);
}

export function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

/** Clamp a possibly-malformed score into [0, 1] for meters. */
export function unit(value: number): number {
  return Number.isFinite(value) ? clamp(value, 0, 1) : 0;
}
