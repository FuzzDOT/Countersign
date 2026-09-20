import {
  forceCenter,
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
  type SimulationLinkDatum,
  type SimulationNodeDatum,
} from 'd3-force';
import type { GraphEdge, GraphNode } from '@/api/types';
import { glyphRadius } from './graphEncoding';
import { clamp } from './format';

export interface SimNode extends SimulationNodeDatum {
  id: string;
  r: number;
}

export interface SimLink extends SimulationLinkDatum<SimNode> {
  id: string;
  confidence: number;
}

export interface Placed {
  x: number;
  y: number;
  r: number;
}

export interface GraphLayout {
  positions: Map<string, Placed>;
  bounds: { minX: number; minY: number; maxX: number; maxY: number };
}

export const GRAPH_WIDTH = 1000;
export const GRAPH_HEIGHT = 700;

/**
 * Identity of a graph for memoisation. The simulation must be re-run only when
 * the set of nodes and edges actually changes, not when a parent re-renders
 * and hands over a fresh array with the same contents. Re-running
 * forceSimulation on every render is what makes a graph visibly jitter
 * (brief §16).
 */
export function graphKey(nodes: readonly GraphNode[], edges: readonly GraphEdge[]): string {
  return `${nodes.map((n) => n.id).join(',')}|${edges.map((e) => e.id).join(',')}`;
}

/**
 * Lay the graph out synchronously and return static positions.
 *
 * Forces (brief §9.1): link distance inversely proportional to edge
 * confidence, many-body at -280, collide at node radius + 4, centre. The
 * simulation is stopped at creation and ticked to rest in one go, so the graph
 * appears settled instead of animating from a heap. Initial positions are laid
 * out on a deterministic spiral, so the same data always produces the same
 * picture.
 */
export function layoutGraph(
  nodes: readonly GraphNode[],
  edges: readonly GraphEdge[],
  width = GRAPH_WIDTH,
  height = GRAPH_HEIGHT,
): GraphLayout {
  const simNodes: SimNode[] = nodes.map((node, index) => {
    const angle = index * 2.399963; // golden angle
    const radius = 14 * Math.sqrt(index + 1);
    return {
      id: node.id,
      r: glyphRadius(node),
      x: width / 2 + radius * Math.cos(angle),
      y: height / 2 + radius * Math.sin(angle),
    };
  });

  const known = new Set(simNodes.map((n) => n.id));
  const simLinks: SimLink[] = edges
    .filter((e) => known.has(e.source) && known.has(e.target) && e.source !== e.target)
    .map((e) => ({ id: e.id, source: e.source, target: e.target, confidence: e.confidence }));

  const simulation = forceSimulation<SimNode>(simNodes)
    .force(
      'link',
      forceLink<SimNode, SimLink>(simLinks)
        .id((d) => d.id)
        .distance((l) => clamp(60 / Math.max(l.confidence, 0.2), 40, 220)),
    )
    .force('charge', forceManyBody<SimNode>().strength(-280))
    .force(
      'collide',
      forceCollide<SimNode>((d) => d.r + 4),
    )
    .force('center', forceCenter<SimNode>(width / 2, height / 2))
    .stop();

  const ticks = Math.ceil(Math.log(simulation.alphaMin()) / Math.log(1 - simulation.alphaDecay()));
  simulation.tick(ticks);

  const positions = new Map<string, Placed>();
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const n of simNodes) {
    const x = n.x ?? width / 2;
    const y = n.y ?? height / 2;
    positions.set(n.id, { x, y, r: n.r });
    minX = Math.min(minX, x - n.r);
    minY = Math.min(minY, y - n.r);
    maxX = Math.max(maxX, x + n.r);
    maxY = Math.max(maxY, y + n.r);
  }
  if (simNodes.length === 0) {
    minX = 0;
    minY = 0;
    maxX = width;
    maxY = height;
  }
  return { positions, bounds: { minX, minY, maxX, maxY } };
}
