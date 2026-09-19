import type { QueryParams } from '@/api/client';
import type { RoutingBucket } from '@/api/types';
import { ENTITY_TYPES, RELATION_TYPES } from './relations';
import { isRoutingBucket } from './routing';

/** Graph filter state, all in the URL (brief §9.3). */

export const DEFAULT_LIMIT_NODES = 300;
export const MAX_LIMIT_NODES = 1200;

export interface GraphFilters {
  entityTypes: string[];
  relations: string[];
  routing: RoutingBucket[];
  minConfidence: number | null;
  rootEntityId: string | null;
  depth: number;
  limitNodes: number;
  /** Index into `cycles[]` when a single cycle is isolated. */
  cycle: number | null;
}

export const EMPTY_GRAPH_FILTERS: GraphFilters = {
  entityTypes: [],
  relations: [],
  routing: [],
  minConfidence: null,
  rootEntityId: null,
  depth: 2,
  limitNodes: DEFAULT_LIMIT_NODES,
  cycle: null,
};

function readInt(value: string | null, min: number, max: number): number | null {
  if (value === null) return null;
  const n = Number.parseInt(value, 10);
  if (!Number.isFinite(n)) return null;
  return Math.min(max, Math.max(min, n));
}

export function parseGraphFilters(params: URLSearchParams): GraphFilters {
  const minConf = params.get('min_confidence');
  const parsedConf = minConf === null ? null : Number(minConf);
  return {
    entityTypes: params.getAll('entity_type').filter((t) => ENTITY_TYPES.includes(t as never)),
    relations: params.getAll('relation').filter((r) => RELATION_TYPES.includes(r as never)),
    routing: params.getAll('routing').filter(isRoutingBucket),
    minConfidence:
      parsedConf !== null && Number.isFinite(parsedConf) ? Math.min(1, Math.max(0, parsedConf)) : null,
    rootEntityId: params.get('root'),
    depth: readInt(params.get('depth'), 1, 3) ?? 2,
    limitNodes: readInt(params.get('limit_nodes'), 10, MAX_LIMIT_NODES) ?? DEFAULT_LIMIT_NODES,
    cycle: readInt(params.get('cycle'), 0, 999),
  };
}

const KEYS = [
  'entity_type',
  'relation',
  'routing',
  'min_confidence',
  'root',
  'depth',
  'limit_nodes',
  'cycle',
];

export function writeGraphFilters(base: URLSearchParams, filters: GraphFilters): URLSearchParams {
  const next = new URLSearchParams(base);
  KEYS.forEach((key) => next.delete(key));
  filters.entityTypes.forEach((t) => next.append('entity_type', t));
  filters.relations.forEach((r) => next.append('relation', r));
  filters.routing.forEach((r) => next.append('routing', r));
  if (filters.minConfidence !== null) next.set('min_confidence', String(filters.minConfidence));
  if (filters.rootEntityId) {
    next.set('root', filters.rootEntityId);
    next.set('depth', String(filters.depth));
  }
  if (filters.limitNodes !== DEFAULT_LIMIT_NODES) next.set('limit_nodes', String(filters.limitNodes));
  if (filters.cycle !== null) next.set('cycle', String(filters.cycle));
  return next;
}

export function activeGraphFilterCount(filters: GraphFilters): number {
  let count = 0;
  if (filters.entityTypes.length > 0) count += 1;
  if (filters.relations.length > 0) count += 1;
  if (filters.routing.length > 0) count += 1;
  if (filters.minConfidence !== null) count += 1;
  if (filters.rootEntityId) count += 1;
  return count;
}

/** Query for GET /graph. `cycle` is a client-side view, not an API param. */
export function toGraphApiQuery(filters: GraphFilters): QueryParams {
  return {
    entity_type: filters.entityTypes,
    relation: filters.relations,
    routing: filters.routing,
    min_confidence: filters.minConfidence,
    root_entity_id: filters.rootEntityId,
    depth: filters.rootEntityId ? filters.depth : null,
    limit_nodes: filters.limitNodes,
  };
}
