import type { RoutingBucket } from '@/api/types';

/**
 * Routing severity is the ONLY thing the --stamp-* colours mean (brief §2.2).
 * Every place that needs a bucket's colour or label reads it from here, so a
 * red thing on screen always means "escalated" and it always carries a word.
 */

export const ROUTING_ORDER: readonly RoutingBucket[] = [
  'escalate_now',
  'flag_for_review',
  'auto_file',
];

export interface RoutingMeta {
  /** Full label, sentence case. */
  label: string;
  /** Short past-tense form for dense rows. */
  short: string;
  /** CSS colour, usable in SVG attributes and inline styles. */
  color: string;
  /** Severity rank, higher is worse. */
  rank: number;
}

export const ROUTING_META: Record<RoutingBucket, RoutingMeta> = {
  escalate_now: { label: 'Escalated', short: 'escalated', color: 'var(--stamp-red)', rank: 3 },
  flag_for_review: {
    label: 'Flagged for review',
    short: 'flagged',
    color: 'var(--stamp-amber)',
    rank: 2,
  },
  auto_file: { label: 'Auto-filed', short: 'auto-filed', color: 'var(--stamp-slate)', rank: 1 },
};

export function isRoutingBucket(value: unknown): value is RoutingBucket {
  return value === 'escalate_now' || value === 'flag_for_review' || value === 'auto_file';
}

export function routingColor(bucket: RoutingBucket): string {
  return ROUTING_META[bucket].color;
}

/** `color-mix` so the token stays the single source of the hue. */
export function tint(color: string, percent: number): string {
  return `color-mix(in srgb, ${color} ${percent}%, transparent)`;
}

export function routingLabel(value: string): string {
  return isRoutingBucket(value) ? ROUTING_META[value].label : value;
}
