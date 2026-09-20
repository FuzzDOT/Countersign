import { describe, expect, it } from 'vitest';
import type { GraphEdge, GraphNode } from '@/api/types';
import { graphKey, layoutGraph } from './graphLayout';

const node = (id: string, risk = 0.5): GraphNode => ({
  id,
  canonical: id,
  entity_type: 'ORG',
  mention_count: 1,
  degree: 1,
  risk,
  flags: [],
});
const edge = (id: string, source: string, target: string): GraphEdge => ({
  id,
  source,
  target,
  relation: 'OWNED_BY',
  confidence: 0.8,
  vacuity: 0.1,
  routing: 'escalate_now',
  insight_ids: [],
  weight: 1,
});

describe('graphKey', () => {
  it('depends on the set of nodes and edges, not on array identity', () => {
    const a = graphKey([node('1'), node('2')], [edge('e1', '1', '2')]);
    const b = graphKey([node('1'), node('2')], [edge('e1', '1', '2')]);
    expect(a).toBe(b);
    expect(graphKey([node('1')], [])).not.toBe(a);
  });
});

describe('layoutGraph', () => {
  const nodes = ['a', 'b', 'c', 'd'].map((id) => node(id));
  const edges = [
    edge('1', 'a', 'b'),
    edge('2', 'b', 'c'),
    edge('3', 'c', 'a'),
    edge('4', 'c', 'd'),
  ];

  it('is deterministic: the same data lays out the same way every time', () => {
    const one = layoutGraph(nodes, edges);
    const two = layoutGraph(nodes, edges);
    expect(one.positions.get('a')).toEqual(two.positions.get('a'));
    expect(one.positions.get('d')).toEqual(two.positions.get('d'));
  });

  it('places every node with finite coordinates and encloses them in the bounds', () => {
    const { positions, bounds } = layoutGraph(nodes, edges);
    expect(positions.size).toBe(4);
    for (const p of positions.values()) {
      expect(Number.isFinite(p.x) && Number.isFinite(p.y)).toBe(true);
      expect(p.x - p.r).toBeGreaterThanOrEqual(bounds.minX - 1e-6);
      expect(p.x + p.r).toBeLessThanOrEqual(bounds.maxX + 1e-6);
    }
  });

  it('keeps nodes from overlapping', () => {
    const { positions } = layoutGraph(nodes, edges);
    const list = [...positions.values()];
    for (let i = 0; i < list.length; i += 1) {
      for (let j = i + 1; j < list.length; j += 1) {
        const a = list[i];
        const b = list[j];
        if (a && b) expect(Math.hypot(a.x - b.x, a.y - b.y)).toBeGreaterThan(a.r + b.r - 2);
      }
    }
  });

  it('ignores edges that point at nodes that are not present, and handles an empty graph', () => {
    expect(() => layoutGraph(nodes, [edge('x', 'a', 'zzz')])).not.toThrow();
    expect(layoutGraph([], []).positions.size).toBe(0);
  });
});
