import { useCallback, useMemo } from 'react';
import { useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useGraph } from '@/api/queries';
import type { GraphEdge } from '@/api/types';
import { useAuth } from '@/auth/useAuth';
import { InsightSheet } from '@/components/domain/InsightSheet';
import { CycleCallout } from '@/components/domain/graph/CycleCallout';
import { EntityPanel } from '@/components/domain/graph/EntityPanel';
import { GraphCanvas } from '@/components/domain/graph/GraphCanvas';
import { GraphFiltersPanel } from '@/components/domain/graph/GraphFiltersPanel';
import { GraphTableView } from '@/components/domain/graph/GraphTableView';
import { Banner } from '@/components/primitives/Banner';
import { Button } from '@/components/primitives/Button';
import { EmptyState } from '@/components/primitives/EmptyState';
import { ErrorState } from '@/components/primitives/ErrorState';
import { SegmentedControl } from '@/components/primitives/SegmentedControl';
import { Skeleton } from '@/components/primitives/Skeleton';
import { useSheetControl } from '@/hooks/useSheetControl';
import {
  EMPTY_GRAPH_FILTERS,
  MAX_LIMIT_NODES,
  activeGraphFilterCount,
  parseGraphFilters,
  writeGraphFilters,
  type GraphFilters,
} from '@/lib/graphFilters';

const VIEW_OPTIONS = [
  { value: 'graph', label: 'Graph' },
  { value: 'table', label: 'Table view' },
] as const;

/**
 * /app/graph and /app/graph/:entityId. The selected entity (route param), the
 * filters, the isolated cycle and the view toggle are all in the URL.
 */
export default function GraphRoute() {
  const { can } = useAuth();
  const { entityId = null } = useParams<{ entityId: string }>();
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const location = useLocation();
  const sheet = useSheetControl();

  const filters = useMemo(() => parseGraphFilters(params), [params]);
  const mode = params.get('view') === 'table' ? 'table' : 'graph';

  // `cycle` is a client-side view over data we already have; keep it out of the query key.
  const apiFilters = useMemo<GraphFilters>(() => ({ ...filters, cycle: null }), [filters]);
  const query = useGraph(apiFilters, can('graph:read'));

  const setFilters = useCallback(
    (next: GraphFilters) => setParams((current) => writeGraphFilters(current, next), { replace: true }),
    [setParams],
  );
  const setMode = (value: 'graph' | 'table') =>
    setParams(
      (current) => {
        const next = new URLSearchParams(current);
        if (value === 'table') next.set('view', 'table');
        else next.delete('view');
        return next;
      },
      { replace: true },
    );

  const nodeHref = useCallback((id: string) => `/app/graph/${id}${location.search}`, [location.search]);
  const selectNode = useCallback((id: string) => navigate(nodeHref(id)), [navigate, nodeHref]);
  const closeEntity = useCallback(
    () => navigate({ pathname: '/app/graph', search: location.search }),
    [navigate, location.search],
  );
  const openSheet = sheet.open;
  const selectEdge = useCallback(
    (edge: GraphEdge) => {
      const first = edge.insight_ids[0];
      if (first) openSheet(first);
    },
    [openSheet],
  );

  const data = query.data;
  const scoped = useMemo(() => {
    if (!data) return null;
    const cycle = filters.cycle !== null ? data.cycles[filters.cycle] : undefined;
    if (!cycle) return { nodes: data.nodes, edges: data.edges };
    const members = new Set(cycle.node_ids);
    return {
      nodes: data.nodes.filter((n) => members.has(n.id)),
      edges: data.edges.filter((e) => members.has(e.source) && members.has(e.target) && e.relation === cycle.relation),
    };
  }, [data, filters.cycle]);

  if (!can('graph:read')) {
    return <EmptyState title="You do not have access to the graph">Ask an owner to grant graph access.</EmptyState>;
  }

  const rootName = filters.rootEntityId
    ? (data?.nodes.find((n) => n.id === filters.rootEntityId)?.canonical ?? null)
    : null;
  const count = activeGraphFilterCount(filters);
  const canRaise = filters.limitNodes < MAX_LIMIT_NODES;

  return (
    <div className="flex h-full flex-col">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-ink-500/40 px-5 py-3">
        <div>
          <h1 className="text-h2 text-ink-50">Risk graph</h1>
          {scoped ? (
            <p className="nums text-body-sm text-ink-200">
              {scoped.nodes.length} entities and {scoped.edges.length} relations
            </p>
          ) : null}
        </div>
        <SegmentedControl ariaLabel="Graph view" options={VIEW_OPTIONS} value={mode} onChange={setMode} />
      </header>

      {data?.truncated ? (
        <div className="px-5 pt-3">
          <Banner>
            Showing {data.nodes.length} entities. The view is capped at {filters.limitNodes}, so some entities and
            relations are hidden.{' '}
            {canRaise ? (
              <Button
                size="sm"
                variant="secondary"
                className="ml-2"
                onClick={() =>
                  setFilters({ ...filters, limitNodes: Math.min(filters.limitNodes * 2, MAX_LIMIT_NODES) })
                }
              >
                Show up to {Math.min(filters.limitNodes * 2, MAX_LIMIT_NODES)}
              </Button>
            ) : (
              'This is the maximum. Narrow the filters to see the rest.'
            )}
          </Banner>
        </div>
      ) : null}

      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto xl:flex-row xl:overflow-hidden">
        {entityId ? (
          <div className="max-h-[50vh] shrink-0 xl:max-h-none xl:w-80">
            <EntityPanel
              entityId={entityId}
              onClose={closeEntity}
              nodeHref={nodeHref}
              onOpenInsight={(id) => sheet.open(id)}
              onShowNeighbourhood={(id) => setFilters({ ...filters, rootEntityId: id })}
            />
          </div>
        ) : null}

        <div className="relative min-h-[420px] min-w-0 flex-1 xl:min-h-0 xl:overflow-y-auto">
          {query.isError && !data ? (
            <ErrorState error={query.error} onRetry={() => void query.refetch()} title="The graph did not load" />
          ) : !scoped ? (
            <Skeleton className="h-full min-h-[420px] w-full" />
          ) : scoped.nodes.length === 0 ? (
            <EmptyState
              title="No entities match"
              action={
                count > 0 ? (
                  <Button variant="secondary" onClick={() => setFilters({ ...EMPTY_GRAPH_FILTERS })}>
                    Clear all filters
                  </Button>
                ) : undefined
              }
            >
              {count > 0 ? 'Loosen a filter to see more of the graph.' : 'Ingest some documents to build the graph.'}
            </EmptyState>
          ) : mode === 'table' ? (
            <GraphTableView
              nodes={scoped.nodes}
              edges={scoped.edges}
              cycles={data?.cycles ?? []}
              nodeHref={nodeHref}
              onOpenInsight={(id) => sheet.open(id)}
            />
          ) : (
            <>
              <GraphCanvas
                nodes={scoped.nodes}
                edges={scoped.edges}
                cycles={data?.cycles ?? []}
                selectedId={entityId}
                onSelectNode={selectNode}
                onSelectEdge={selectEdge}
              />
              <GraphLegend />
            </>
          )}
        </div>

        <aside aria-label="Graph controls" className="flex shrink-0 flex-col gap-4 border-t border-ink-500/40 p-4 xl:w-72 xl:overflow-y-auto xl:border-l xl:border-t-0">
          <CycleCallout
            cycles={data?.cycles ?? []}
            nodes={data?.nodes ?? []}
            focusedIndex={filters.cycle}
            onFocus={(index) => setFilters({ ...filters, cycle: index })}
          />
          <GraphFiltersPanel
            filters={filters}
            onChange={setFilters}
            onClear={() => setFilters({ ...EMPTY_GRAPH_FILTERS, limitNodes: filters.limitNodes })}
            activeCount={count}
            rootName={rootName}
          />
        </aside>
      </div>

      <InsightSheet insightId={sheet.insightId} onClose={sheet.close} />
    </div>
  );
}

/** What the shapes and line styles mean. Colour is only ever routing severity. */
function GraphLegend() {
  return (
    <details className="absolute bottom-3 left-3 max-w-xs rounded-panel border border-ink-500/40 bg-ink-900/90 text-body-sm text-ink-200">
      <summary className="cursor-pointer px-3 py-2 font-medium hover:text-ink-50">How to read this graph</summary>
      <ul className="flex flex-col gap-1 border-t border-ink-500/40 px-3 py-2">
        <li>Square organisation, circle person, diamond account, small circle other.</li>
        <li>Larger means higher risk. Fainter means fewer mentions. A ring means flagged.</li>
        <li>Solid line is money moving, dashed is ownership, dotted is a shared attribute.</li>
        <li>Line colour is routing: red escalated, amber flagged, grey auto-filed.</li>
        <li>Thicker means more supporting insights. Fainter means lower confidence.</li>
      </ul>
    </details>
  );
}
