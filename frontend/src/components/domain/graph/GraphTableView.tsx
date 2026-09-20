import { useMemo } from 'react';
import { Link } from 'react-router-dom';
import type { GraphCycle, GraphEdge, GraphNode } from '@/api/types';
import { Button } from '@/components/primitives/Button';
import { DataTable, type Column } from '@/components/primitives/Table';
import { formatScore } from '@/lib/format';
import { entityTypeLabel, relationLabel } from '@/lib/relations';
import { ROUTING_META } from '@/lib/routing';
import { RoutingBadge } from '../RoutingBadge';

interface GraphTableViewProps {
  nodes: readonly GraphNode[];
  edges: readonly GraphEdge[];
  cycles: readonly GraphCycle[];
  /** Builds the link for an entity, preserving the current filters. */
  nodeHref: (id: string) => string;
  onOpenInsight: (id: string) => void;
}

/**
 * The accessible alternative to the force graph (brief §9.4): the same nodes
 * and edges as two sortable tables, with the same filters applied upstream and
 * rows that lead to the same details. Not a stub.
 */
export function GraphTableView({
  nodes,
  edges,
  cycles,
  nodeHref,
  onOpenInsight,
}: GraphTableViewProps) {
  const nameById = useMemo(() => new Map(nodes.map((n) => [n.id, n.canonical])), [nodes]);
  const cycleNodes = useMemo(() => new Set(cycles.flatMap((c) => c.node_ids)), [cycles]);

  const nodeColumns: Column<GraphNode>[] = [
    {
      key: 'name',
      header: 'Entity',
      sortValue: (n) => n.canonical.toLowerCase(),
      render: (n) => (
        <Link
          to={nodeHref(n.id)}
          className="font-medium text-ink-50 underline-offset-4 hover:underline"
        >
          {n.canonical}
        </Link>
      ),
    },
    {
      key: 'type',
      header: 'Type',
      sortValue: (n) => n.entity_type,
      render: (n) => entityTypeLabel(n.entity_type),
    },
    {
      key: 'risk',
      header: 'Risk',
      align: 'right',
      sortValue: (n) => n.risk,
      render: (n) => formatScore(n.risk),
    },
    {
      key: 'degree',
      header: 'Connections',
      align: 'right',
      sortValue: (n) => n.degree,
      render: (n) => n.degree,
    },
    {
      key: 'mentions',
      header: 'Mentions',
      align: 'right',
      sortValue: (n) => n.mention_count,
      render: (n) => n.mention_count,
    },
    {
      key: 'flags',
      header: 'Flags',
      render: (n) => {
        const flags = [...n.flags];
        if (cycleNodes.has(n.id) && !flags.includes('ownership_cycle'))
          flags.push('ownership_cycle');
        return flags.length ? (
          flags.map((f) => f.replace(/_/g, ' ')).join(', ')
        ) : (
          <span className="text-ink-200">none</span>
        );
      },
    },
  ];

  const edgeColumns: Column<GraphEdge>[] = [
    {
      key: 'from',
      header: 'From',
      sortValue: (e) => (nameById.get(e.source) ?? '').toLowerCase(),
      render: (e) => (
        <Link to={nodeHref(e.source)} className="underline-offset-4 hover:underline">
          {nameById.get(e.source) ?? e.source}
        </Link>
      ),
    },
    {
      key: 'relation',
      header: 'Relation',
      sortValue: (e) => e.relation,
      render: (e) => relationLabel(e.relation),
    },
    {
      key: 'to',
      header: 'To',
      sortValue: (e) => (nameById.get(e.target) ?? '').toLowerCase(),
      render: (e) => (
        <Link to={nodeHref(e.target)} className="underline-offset-4 hover:underline">
          {nameById.get(e.target) ?? e.target}
        </Link>
      ),
    },
    {
      key: 'conf',
      header: 'Confidence',
      align: 'right',
      sortValue: (e) => e.confidence,
      render: (e) => formatScore(e.confidence),
    },
    {
      key: 'vac',
      header: 'Vacuity',
      align: 'right',
      sortValue: (e) => e.vacuity,
      render: (e) => formatScore(e.vacuity),
    },
    {
      key: 'routing',
      header: 'Routing',
      sortValue: (e) => ROUTING_META[e.routing].rank,
      render: (e) => <RoutingBadge bucket={e.routing} />,
    },
    {
      key: 'weight',
      header: 'Supporting insights',
      align: 'right',
      sortValue: (e) => e.weight,
      render: (e) => e.weight,
    },
    {
      key: 'open',
      header: 'Detail',
      render: (e) => {
        const first = e.insight_ids[0];
        return first ? (
          <Button size="sm" variant="secondary" onClick={() => onOpenInsight(first)}>
            Open insight
          </Button>
        ) : (
          <span className="text-ink-200">none</span>
        );
      },
    },
  ];

  return (
    <div className="flex flex-col gap-8 p-5">
      <section aria-labelledby="graph-table-nodes" className="flex flex-col gap-2">
        <h2 id="graph-table-nodes" className="text-h3 text-ink-50">
          Entities ({nodes.length})
        </h2>
        <DataTable
          caption="Entities in the risk graph"
          columns={nodeColumns}
          rows={nodes}
          rowKey={(n) => n.id}
          initialSort={{ key: 'risk', dir: 'desc' }}
          maxHeight={420}
        />
      </section>
      <section aria-labelledby="graph-table-edges" className="flex flex-col gap-2">
        <h2 id="graph-table-edges" className="text-h3 text-ink-50">
          Relations ({edges.length})
        </h2>
        <DataTable
          caption="Relations in the risk graph"
          columns={edgeColumns}
          rows={edges}
          rowKey={(e) => e.id}
          initialSort={{ key: 'routing', dir: 'desc' }}
          maxHeight={520}
        />
      </section>
    </div>
  );
}
