import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type RefObject } from 'react';
import { select } from 'd3-selection';
import { zoom, zoomIdentity, zoomTransform, type D3ZoomEvent, type ZoomBehavior } from 'd3-zoom';
import type { GraphCycle, GraphEdge, GraphNode } from '@/api/types';
import { Button } from '@/components/primitives/Button';
import { FitIcon } from '@/components/primitives/icons';
import { clamp, formatScore } from '@/lib/format';
import { confidenceOpacity, makeEdgeWidth, makeMentionOpacity } from '@/lib/graphEncoding';
import { GRAPH_HEIGHT, GRAPH_WIDTH, graphKey, layoutGraph, type Placed } from '@/lib/graphLayout';
import { dashFor, entityTypeLabel, relationKind, relationVerb } from '@/lib/relations';
import { routingColor } from '@/lib/routing';
import { NodeGlyph } from './NodeGlyph';

interface GraphCanvasProps {
  nodes: readonly GraphNode[];
  edges: readonly GraphEdge[];
  cycles: readonly GraphCycle[];
  selectedId: string | null;
  onSelectNode: (id: string) => void;
  onSelectEdge: (edge: GraphEdge) => void;
}

interface HoverState {
  id: string;
  x: number;
  y: number;
}

const SCALE_MIN = 0.4;
const SCALE_MAX = 4;
/** Below this many nodes every label is shown; above it labels appear when zoomed in. */
const ALWAYS_LABEL_UNDER = 40;

interface EdgeGlyphProps {
  edge: GraphEdge;
  a: Placed;
  b: Placed;
  width: number;
  dimmed: boolean;
  halo: boolean;
  names: string;
  onSelect: (edge: GraphEdge) => void;
}

const EdgeGlyph = memo(function EdgeGlyph({ edge, a, b, width, dimmed, halo, names, onSelect }: EdgeGlyphProps) {
  const kind = relationKind(edge.relation);
  const dash = dashFor(kind);
  const line = { x1: a.x, y1: a.y, x2: b.x, y2: b.y };
  return (
    <g opacity={dimmed ? 0.2 : 1} data-edge-id={edge.id}>
      {halo ? (
        <line {...line} stroke="var(--stamp-red)" strokeOpacity={0.25} strokeWidth={width + 4} strokeLinecap="round" />
      ) : null}
      <line
        {...line}
        stroke={routingColor(edge.routing)}
        strokeOpacity={confidenceOpacity(edge.confidence)}
        strokeWidth={width}
        strokeDasharray={dash}
        strokeLinecap={kind === 'shared' ? 'round' : 'butt'}
      />
      {/* Wide invisible hit target so a hairline is still clickable. */}
      <line
        {...line}
        stroke="transparent"
        strokeWidth={12}
        style={{ cursor: 'pointer', pointerEvents: 'stroke' }}
        onClick={() => onSelect(edge)}
      >
        <title>{`${names}: ${relationVerb(edge.relation)}, confidence ${formatScore(edge.confidence)}`}</title>
      </line>
    </g>
  );
});

interface NodeElProps {
  node: GraphNode;
  at: Placed;
  fillOpacity: number;
  selected: boolean;
  dimmed: boolean;
  forceLabel: boolean;
  onSelect: (id: string) => void;
  onHover: (state: HoverState | null) => void;
  containerRef: RefObject<HTMLDivElement>;
}

const NodeEl = memo(function NodeEl({
  node,
  at,
  fillOpacity,
  selected,
  dimmed,
  forceLabel,
  onSelect,
  onHover,
  containerRef,
}: NodeElProps) {
  const report = (event: { clientX: number; clientY: number }) => {
    const rect = containerRef.current?.getBoundingClientRect();
    onHover({ id: node.id, x: event.clientX - (rect?.left ?? 0), y: event.clientY - (rect?.top ?? 0) });
  };
  return (
    <g
      role="button"
      tabIndex={0}
      aria-label={`${node.canonical}, ${entityTypeLabel(node.entity_type)}, risk ${formatScore(node.risk)}`}
      aria-pressed={selected}
      transform={`translate(${at.x},${at.y})`}
      opacity={dimmed ? 0.2 : 1}
      style={{ cursor: 'pointer' }}
      data-node-id={node.id}
      onClick={() => onSelect(node.id)}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          onSelect(node.id);
        }
      }}
      onPointerEnter={report}
      onPointerMove={report}
      onPointerLeave={() => onHover(null)}
      onFocus={() => onHover({ id: node.id, x: 0, y: 0 })}
      onBlur={() => onHover(null)}
    >
      <NodeGlyph
        entityType={node.entity_type}
        r={at.r}
        fillOpacity={fillOpacity}
        hasFlags={node.flags.length > 0}
        selected={selected}
      />
      <text
        y={at.r + 13}
        textAnchor="middle"
        fontSize={11}
        fill="var(--ink-050)"
        stroke="var(--ink-900)"
        strokeWidth={3}
        style={{ paintOrder: 'stroke' }}
        className={forceLabel ? 'graph-label graph-label--always' : 'graph-label'}
      >
        {node.canonical}
      </text>
    </g>
  );
});

/**
 * The risk graph (brief §9). d3-force lays it out once per distinct
 * node/edge set, d3-zoom pans and zooms (0.4 to 4), and React renders plain
 * SVG so hit-testing and accessibility come free.
 *
 * Cycles come from the server. This component never detects a cycle: it only
 * looks up which drawn edges belong to a server-provided one, to halo them.
 */
export function GraphCanvas({ nodes, edges, cycles, selectedId, onSelectNode, onSelectEdge }: GraphCanvasProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const viewRef = useRef<SVGGElement>(null);
  const zoomRef = useRef<ZoomBehavior<SVGSVGElement, unknown> | null>(null);
  const skipCenter = useRef(false);
  const [hover, setHover] = useState<HoverState | null>(null);

  // Memoised on identity of the node and edge SETS, not on the array objects.
  const key = graphKey(nodes, edges);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const layout = useMemo(() => layoutGraph(nodes, edges), [key]);

  const mentionOpacity = useMemo(() => makeMentionOpacity(nodes), [nodes]);
  const edgeWidth = useMemo(() => makeEdgeWidth(edges), [edges]);
  const nodeById = useMemo(() => new Map(nodes.map((n) => [n.id, n])), [nodes]);

  // Edges belonging to a server-provided cycle. A lookup, not detection.
  const cycleEdgeIds = useMemo(() => {
    const ids = new Set<string>();
    for (const cycle of cycles) {
      const members = new Set(cycle.node_ids);
      for (const edge of edges) {
        if (edge.relation === cycle.relation && members.has(edge.source) && members.has(edge.target)) {
          ids.add(edge.id);
        }
      }
    }
    return ids;
  }, [cycles, edges]);
  const cycleNodeIds = useMemo(() => new Set(cycles.flatMap((c) => c.node_ids)), [cycles]);

  // Hover: incident edges raise, everything else drops to 20%.
  const incident = useMemo(() => {
    const edgeIds = new Set<string>();
    const nodeIds = new Set<string>();
    if (hover) {
      nodeIds.add(hover.id);
      for (const edge of edges) {
        if (edge.source === hover.id || edge.target === hover.id) {
          edgeIds.add(edge.id);
          nodeIds.add(edge.source);
          nodeIds.add(edge.target);
        }
      }
    }
    return { edgeIds, nodeIds };
  }, [hover, edges]);

  const orderedEdges = useMemo(
    () =>
      hover
        ? [...edges].sort((a, b) => Number(incident.edgeIds.has(a.id)) - Number(incident.edgeIds.has(b.id)))
        : edges,
    [edges, hover, incident],
  );

  // Order nodes so the riskiest paint (and tab) first.
  const orderedNodes = useMemo(() => [...nodes].sort((a, b) => b.risk - a.risk), [nodes]);

  // ── zoom and pan ─────────────────────────────────────────────────────────
  // A layout effect, declared before the fit effect below, so the behaviour
  // exists by the time "fit to content" runs on first mount.
  useLayoutEffect(() => {
    const svgEl = svgRef.current;
    const view = viewRef.current;
    if (!svgEl || !view) return undefined;
    const behavior = zoom<SVGSVGElement, unknown>()
      .scaleExtent([SCALE_MIN, SCALE_MAX])
      .on('zoom', (event: D3ZoomEvent<SVGSVGElement, unknown>) => {
        view.setAttribute('transform', event.transform.toString());
        svgEl.dataset['labels'] = event.transform.k >= 1.2 ? 'on' : 'off';
      });
    select(svgEl).call(behavior);
    zoomRef.current = behavior;
    return () => {
      select(svgEl).on('.zoom', null);
      zoomRef.current = null;
    };
  }, []);

  const fit = useCallback(() => {
    const svgEl = svgRef.current;
    const behavior = zoomRef.current;
    if (!svgEl || !behavior) return;
    const { minX, minY, maxX, maxY } = layout.bounds;
    const pad = 48;
    const bw = Math.max(maxX - minX + pad * 2, 1);
    const bh = Math.max(maxY - minY + pad * 2, 1);
    const k = clamp(Math.min(GRAPH_WIDTH / bw, GRAPH_HEIGHT / bh, 1.5), SCALE_MIN, SCALE_MAX);
    const cx = (minX + maxX) / 2;
    const cy = (minY + maxY) / 2;
    behavior.transform(
      select(svgEl),
      zoomIdentity.translate(GRAPH_WIDTH / 2 - k * cx, GRAPH_HEIGHT / 2 - k * cy).scale(k),
    );
  }, [layout]);

  // "Fit to content" on first load and whenever the data set changes.
  useLayoutEffect(() => {
    fit();
  }, [fit]);

  // Bring an externally selected entity (deep link, table view) to the centre.
  useEffect(() => {
    if (skipCenter.current) {
      skipCenter.current = false;
      return;
    }
    const svgEl = svgRef.current;
    const behavior = zoomRef.current;
    const at = selectedId ? layout.positions.get(selectedId) : undefined;
    if (!svgEl || !behavior || !at) return;
    const k = Math.max(zoomTransform(svgEl).k, 1);
    behavior.transform(
      select(svgEl),
      zoomIdentity.translate(GRAPH_WIDTH / 2 - k * at.x, GRAPH_HEIGHT / 2 - k * at.y).scale(k),
    );
  }, [selectedId, layout]);

  const selectNode = useCallback(
    (id: string) => {
      skipCenter.current = true; // the user is already looking at it
      onSelectNode(id);
    },
    [onSelectNode],
  );

  const hovered = hover ? nodeById.get(hover.id) : undefined;
  const showAllLabels = nodes.length < ALWAYS_LABEL_UNDER;

  return (
    <div ref={containerRef} className="relative h-full min-h-[420px] w-full overflow-hidden bg-ink-900">
      <svg
        ref={svgRef}
        role="group"
        aria-label={`Risk graph with ${nodes.length} entities and ${edges.length} relations. A table view is available for keyboard and screen reader use.`}
        viewBox={`0 0 ${GRAPH_WIDTH} ${GRAPH_HEIGHT}`}
        preserveAspectRatio="xMidYMid meet"
        className="graph-svg block h-full w-full touch-none"
        data-labels="off"
      >
        <g ref={viewRef}>
          <g>
            {orderedEdges.map((edge) => {
              const a = layout.positions.get(edge.source);
              const b = layout.positions.get(edge.target);
              if (!a || !b) return null;
              const from = nodeById.get(edge.source)?.canonical ?? '';
              const to = nodeById.get(edge.target)?.canonical ?? '';
              return (
                <EdgeGlyph
                  key={edge.id}
                  edge={edge}
                  a={a}
                  b={b}
                  width={edgeWidth(edge.weight)}
                  dimmed={hover !== null && !incident.edgeIds.has(edge.id)}
                  halo={cycleEdgeIds.has(edge.id)}
                  names={`${from} to ${to}`}
                  onSelect={onSelectEdge}
                />
              );
            })}
          </g>
          <g>
            {orderedNodes.map((node) => {
              const at = layout.positions.get(node.id);
              if (!at) return null;
              return (
                <NodeEl
                  key={node.id}
                  node={node}
                  at={at}
                  fillOpacity={mentionOpacity(node.mention_count)}
                  selected={node.id === selectedId}
                  dimmed={hover !== null && !incident.nodeIds.has(node.id)}
                  forceLabel={showAllLabels || cycleNodeIds.has(node.id) || node.id === selectedId || node.id === hover?.id}
                  onSelect={selectNode}
                  onHover={setHover}
                  containerRef={containerRef}
                />
              );
            })}
          </g>
        </g>
      </svg>

      <div className="absolute right-3 top-3">
        <Button variant="secondary" size="sm" onClick={fit}>
          <FitIcon />
          Reset view
        </Button>
      </div>

      {hovered && hover && (hover.x !== 0 || hover.y !== 0) ? (
        <div
          role="tooltip"
          className="pointer-events-none absolute z-10 max-w-64 rounded-soft border border-ctl-border bg-ink-900 px-3 py-2 text-body-sm text-ink-50 shadow-overlay"
          style={{ left: Math.min(hover.x + 14, (containerRef.current?.clientWidth ?? 600) - 260), top: hover.y + 14 }}
        >
          <p className="font-semibold">{hovered.canonical}</p>
          <p className="text-ink-200">{entityTypeLabel(hovered.entity_type)}</p>
          <p className="nums text-ink-200">
            Risk {formatScore(hovered.risk)}, {hovered.degree} connections
          </p>
          {hovered.flags.length > 0 ? (
            <p className="text-ink-200">Flags: {hovered.flags.map((f) => f.replace(/_/g, ' ')).join(', ')}</p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
