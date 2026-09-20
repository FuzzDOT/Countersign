import { scaleLinear, scaleSqrt } from 'd3-scale';
import type { GraphEdge, GraphNode } from '@/api/types';
import { unit } from './format';

/**
 * Visual encoding for the risk graph (brief §9.2). Each data field maps to one
 * visual channel, and colour is spent only on edge routing severity.
 *
 *   entity_type   shape          risk          radius (sqrt, 6 to 20px)
 *   mention_count fill opacity   flags         hairline outer ring
 *   edge.weight   stroke width   confidence    stroke opacity (35 to 100%)
 */

export type NodeShapeKind = 'square' | 'circle' | 'diamond' | 'dot';

export function shapeFor(entityType: string): NodeShapeKind {
  switch (entityType) {
    case 'ORG':
      return 'square';
    case 'PERSON':
      return 'circle';
    case 'ACCOUNT_REF':
      return 'diamond';
    default:
      return 'dot';
  }
}

const radiusScale = scaleSqrt().domain([0, 1]).range([6, 20]).clamp(true);

export function nodeRadius(risk: number): number {
  return radiusScale(unit(risk));
}

/** Everything that is not ORG, PERSON or ACCOUNT_REF is a small circle. */
export function glyphRadius(node: Pick<GraphNode, 'risk' | 'entity_type'>): number {
  const r = nodeRadius(node.risk);
  return shapeFor(node.entity_type) === 'dot' ? Math.max(5, r * 0.6) : r;
}

export function makeMentionOpacity(nodes: readonly GraphNode[]): (mentions: number) => number {
  const counts = nodes.map((n) => n.mention_count);
  const min = counts.length ? Math.min(...counts) : 0;
  const max = counts.length ? Math.max(...counts) : 1;
  const scale = scaleLinear()
    .domain([min, max === min ? min + 1 : max])
    .range([0.4, 1])
    .clamp(true);
  return (mentions) => scale(mentions);
}

export function makeEdgeWidth(edges: readonly GraphEdge[]): (weight: number) => number {
  const weights = edges.map((e) => e.weight);
  const max = weights.length ? Math.max(...weights) : 1;
  const scale = scaleLinear()
    .domain([1, max <= 1 ? 2 : max])
    .range([1, 4])
    .clamp(true);
  return (weight) => scale(weight);
}

export function confidenceOpacity(confidence: number): number {
  return 0.35 + 0.65 * unit(confidence);
}
