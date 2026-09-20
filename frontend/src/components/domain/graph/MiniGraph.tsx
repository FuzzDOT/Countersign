import { useMemo } from 'react';
import { Link } from 'react-router-dom';
import { useGraph } from '@/api/queries';
import type { GraphNeighborhood } from '@/api/types';
import { ErrorState } from '@/components/primitives/ErrorState';
import { Skeleton } from '@/components/primitives/Skeleton';
import { confidenceOpacity, makeEdgeWidth, makeMentionOpacity } from '@/lib/graphEncoding';
import { graphKey, layoutGraph } from '@/lib/graphLayout';
import { EMPTY_GRAPH_FILTERS } from '@/lib/graphFilters';
import { dashFor, relationKind } from '@/lib/relations';
import { routingColor } from '@/lib/routing';
import { NodeGlyph } from './NodeGlyph';

const W = 480;
const H = 240;

interface MiniGraphProps {
  subjectId: string;
  subjectName: string;
  neighborhood: GraphNeighborhood;
}

/**
 * A 240px-tall static picture of the insight's graph neighbourhood, linking
 * through to the full canvas focused on the subject (brief §7.5).
 */
export function MiniGraph({ subjectId, subjectName, neighborhood }: MiniGraphProps) {
  const filters = useMemo(
    () => ({
      ...EMPTY_GRAPH_FILTERS,
      rootEntityId: subjectId,
      depth: neighborhood.depth,
      limitNodes: 120,
    }),
    [subjectId, neighborhood.depth],
  );
  const query = useGraph(filters);

  const scoped = useMemo(() => {
    const data = query.data;
    if (!data) return null;
    const allow = new Set(neighborhood.node_ids);
    const nodes = allow.size > 0 ? data.nodes.filter((n) => allow.has(n.id)) : data.nodes;
    const ids = new Set(nodes.map((n) => n.id));
    const edges = data.edges.filter((e) => ids.has(e.source) && ids.has(e.target));
    return { nodes, edges };
  }, [query.data, neighborhood.node_ids]);

  const key = scoped ? graphKey(scoped.nodes, scoped.edges) : '';
  const layout = useMemo(
    () => (scoped ? layoutGraph(scoped.nodes, scoped.edges, W, H) : null),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [key],
  );

  if (query.isLoading) return <Skeleton className="h-60 w-full" />;
  if (query.isError)
    return (
      <ErrorState
        compact
        error={query.error}
        onRetry={() => void query.refetch()}
        title="Graph context did not load"
      />
    );
  if (!scoped || !layout || scoped.nodes.length === 0) {
    return (
      <p className="text-body-sm text-ink-200">
        No graph neighbourhood is recorded for this insight.
      </p>
    );
  }

  const { bounds } = layout;
  const pad = 24;
  const bw = bounds.maxX - bounds.minX + pad * 2;
  const bh = bounds.maxY - bounds.minY + pad * 2;
  const k = Math.min(W / bw, H / bh, 1.4);
  const tx = W / 2 - (k * (bounds.minX + bounds.maxX)) / 2;
  const ty = H / 2 - (k * (bounds.minY + bounds.maxY)) / 2;
  const mention = makeMentionOpacity(scoped.nodes);
  const width = makeEdgeWidth(scoped.edges);

  return (
    <Link
      to={`/app/graph/${subjectId}`}
      aria-label={`Open the full graph focused on ${subjectName}. ${scoped.nodes.length} entities in this neighbourhood.`}
      className="block h-60 overflow-hidden rounded-panel border border-ink-500/40 bg-ink-900 hover:border-ink-200/60"
    >
      <svg viewBox={`0 0 ${W} ${H}`} className="block h-full w-full" aria-hidden="true">
        <g transform={`translate(${tx},${ty}) scale(${k})`}>
          {scoped.edges.map((edge) => {
            const a = layout.positions.get(edge.source);
            const b = layout.positions.get(edge.target);
            if (!a || !b) return null;
            const kind = relationKind(edge.relation);
            return (
              <line
                key={edge.id}
                x1={a.x}
                y1={a.y}
                x2={b.x}
                y2={b.y}
                stroke={routingColor(edge.routing)}
                strokeOpacity={confidenceOpacity(edge.confidence)}
                strokeWidth={width(edge.weight)}
                strokeDasharray={dashFor(kind)}
              />
            );
          })}
          {scoped.nodes.map((node) => {
            const at = layout.positions.get(node.id);
            if (!at) return null;
            return (
              <g key={node.id} transform={`translate(${at.x},${at.y})`}>
                <NodeGlyph
                  entityType={node.entity_type}
                  r={at.r}
                  fillOpacity={mention(node.mention_count)}
                  hasFlags={node.flags.length > 0}
                  selected={node.id === subjectId}
                />
              </g>
            );
          })}
        </g>
      </svg>
    </Link>
  );
}
