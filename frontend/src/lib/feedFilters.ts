import type { InsightSort, Resolver, RoutingBucket } from '@/api/types';
import type { QueryParams } from '@/api/client';
import { RELATION_TYPES } from './relations';
import { isRoutingBucket } from './routing';

/**
 * Every feed filter lives in the URL (brief §7.4): a filtered view is
 * shareable and survives a reload. This module is the one place that maps
 * URLSearchParams to a typed object and back.
 *
 * URL parameter names are the API's own, so the mapping is obvious.
 */

export const SORT_OPTIONS: readonly { value: InsightSort; label: string }[] = [
  { value: '-created_at', label: 'Newest first' },
  { value: '-confidence', label: 'Highest confidence' },
  { value: 'confidence', label: 'Lowest confidence' },
  { value: '-vacuity', label: 'Most uncertain' },
  { value: '-fragility', label: 'Most fragile' },
];

const SORT_VALUES = new Set<string>([
  'created_at',
  '-created_at',
  'confidence',
  '-confidence',
  'vacuity',
  '-vacuity',
  'fragility',
  '-fragility',
]);

export interface FeedFilters {
  routing: RoutingBucket[];
  resolvedBy: Resolver | null;
  minConfidence: number | null;
  maxConfidence: number | null;
  minVacuity: number | null;
  relation: string[];
  sort: InsightSort;
  q: string;
  entityId: string | null;
  documentId: string | null;
}

export const DEFAULT_SORT: InsightSort = '-created_at';

export const EMPTY_FILTERS: FeedFilters = {
  routing: [],
  resolvedBy: null,
  minConfidence: null,
  maxConfidence: null,
  minVacuity: null,
  relation: [],
  sort: DEFAULT_SORT,
  q: '',
  entityId: null,
  documentId: null,
};

function readUnit(value: string | null): number | null {
  if (value === null || value.trim() === '') return null;
  const n = Number(value);
  if (!Number.isFinite(n)) return null;
  return Math.min(1, Math.max(0, n));
}

export function parseFeedFilters(params: URLSearchParams): FeedFilters {
  const routing = params.getAll('routing').filter(isRoutingBucket);
  const resolved = params.get('resolved_by');
  const relation = params.getAll('relation').filter((r) => RELATION_TYPES.includes(r as never));
  const sort = params.get('sort');
  return {
    routing: Array.from(new Set(routing)),
    resolvedBy: resolved === 'classical' || resolved === 'nemotron' ? resolved : null,
    minConfidence: readUnit(params.get('min_confidence')),
    maxConfidence: readUnit(params.get('max_confidence')),
    minVacuity: readUnit(params.get('min_vacuity')),
    relation: Array.from(new Set(relation)),
    sort: sort !== null && SORT_VALUES.has(sort) ? (sort as InsightSort) : DEFAULT_SORT,
    q: (params.get('q') ?? '').slice(0, 200),
    entityId: params.get('entity_id'),
    documentId: params.get('document_id'),
  };
}

/** Write filters into an existing params object, preserving unrelated keys (like `insight`). */
export function writeFeedFilters(base: URLSearchParams, filters: FeedFilters): URLSearchParams {
  const next = new URLSearchParams(base);
  for (const key of [
    'routing',
    'resolved_by',
    'min_confidence',
    'max_confidence',
    'min_vacuity',
    'relation',
    'sort',
    'q',
    'entity_id',
    'document_id',
  ]) {
    next.delete(key);
  }
  filters.routing.forEach((r) => next.append('routing', r));
  if (filters.resolvedBy) next.set('resolved_by', filters.resolvedBy);
  if (filters.minConfidence !== null) next.set('min_confidence', String(filters.minConfidence));
  if (filters.maxConfidence !== null) next.set('max_confidence', String(filters.maxConfidence));
  if (filters.minVacuity !== null) next.set('min_vacuity', String(filters.minVacuity));
  filters.relation.forEach((r) => next.append('relation', r));
  if (filters.sort !== DEFAULT_SORT) next.set('sort', filters.sort);
  if (filters.q.trim() !== '') next.set('q', filters.q);
  if (filters.entityId) next.set('entity_id', filters.entityId);
  if (filters.documentId) next.set('document_id', filters.documentId);
  return next;
}

/** How many filters are active. Sort is a preference, not a filter. */
export function activeFilterCount(filters: FeedFilters): number {
  let count = 0;
  if (filters.routing.length > 0) count += 1;
  if (filters.resolvedBy) count += 1;
  if (filters.minConfidence !== null || filters.maxConfidence !== null) count += 1;
  if (filters.minVacuity !== null) count += 1;
  if (filters.relation.length > 0) count += 1;
  if (filters.q.trim() !== '') count += 1;
  if (filters.entityId) count += 1;
  if (filters.documentId) count += 1;
  return count;
}

/** Query string for GET /insights (minus cursor). */
export function toApiQuery(filters: FeedFilters): QueryParams {
  return {
    routing: filters.routing,
    resolved_by: filters.resolvedBy,
    min_confidence: filters.minConfidence,
    max_confidence: filters.maxConfidence,
    min_vacuity: filters.minVacuity,
    relation: filters.relation,
    entity_id: filters.entityId,
    document_id: filters.documentId,
    q: filters.q.trim() === '' ? null : filters.q.trim(),
    sort: filters.sort,
  };
}
