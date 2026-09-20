import { useCallback, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useInsightsInfinite, useInsightStats } from '@/api/queries';
import type { RoutingBucket } from '@/api/types';
import { useAuth } from '@/auth/useAuth';
import { FeedFiltersPanel } from '@/components/domain/feed/FeedFiltersPanel';
import { FeedStatsHeader } from '@/components/domain/feed/FeedStatsHeader';
import { InsightList } from '@/components/domain/feed/InsightList';
import { InsightSheet } from '@/components/domain/InsightSheet';
import { Banner } from '@/components/primitives/Banner';
import { Button, LinkButton } from '@/components/primitives/Button';
import { EmptyState } from '@/components/primitives/EmptyState';
import { ErrorState } from '@/components/primitives/ErrorState';
import { CloseIcon, FilterIcon } from '@/components/primitives/icons';
import { Sheet } from '@/components/primitives/Sheet';
import { Skeleton } from '@/components/primitives/Skeleton';
import { IconButton } from '@/components/primitives/IconButton';
import { useMediaQuery } from '@/hooks/useMediaQuery';
import { useSheetControl } from '@/hooks/useSheetControl';
import { useUpstreamStatus } from '@/hooks/useUpstreamStatus';
import { dismissNemotronBanner } from '@/lib/upstreamStatus';
import {
  EMPTY_FILTERS,
  activeFilterCount,
  parseFeedFilters,
  writeFeedFilters,
  type FeedFilters,
} from '@/lib/feedFilters';

/**
 * /app/feed and /app/feed/:insightId. Filters, sort and the open insight are
 * all in the URL, so any view is shareable and a reload puts you back where
 * you were (brief §7).
 */
export default function FeedRoute() {
  const { can } = useAuth();
  const [params, setParams] = useSearchParams();
  const wide = useMediaQuery('(min-width: 1280px)');
  const [filtersOpen, setFiltersOpen] = useState(false);
  const sheet = useSheetControl();
  const upstream = useUpstreamStatus();

  const filters = useMemo(() => parseFeedFilters(params), [params]);
  const count = activeFilterCount(filters);

  const setFilters = useCallback(
    (next: FeedFilters) =>
      setParams((current) => writeFeedFilters(current, next), { replace: true }),
    [setParams],
  );
  const clear = useCallback(
    () => setFilters({ ...EMPTY_FILTERS, sort: filters.sort }),
    [setFilters, filters.sort],
  );
  const toggleRouting = useCallback(
    (bucket: RoutingBucket) => {
      const only = filters.routing.length === 1 && filters.routing[0] === bucket;
      setFilters({ ...filters, routing: only ? [] : [bucket] });
    },
    [filters, setFilters],
  );

  const stats = useInsightStats();
  const insights = useInsightsInfinite(filters);
  const rows = useMemo(
    () => insights.data?.pages.flatMap((page) => page.data) ?? [],
    [insights.data],
  );
  const fetchNext = useCallback(() => {
    void insights.fetchNextPage();
  }, [insights]);

  const anyDegraded = rows.some((row) => row.degraded);
  const showBanner =
    (upstream.nemotronDown && !upstream.dismissed) || (anyDegraded && !upstream.dismissed);

  if (!can('insights:read')) {
    return (
      <EmptyState title="You do not have access to the feed">
        Ask an owner to grant insight access.
      </EmptyState>
    );
  }

  const panel = (
    <FeedFiltersPanel
      filters={filters}
      onChange={setFilters}
      onClear={clear}
      activeCount={count}
      stats={stats.data}
    />
  );

  return (
    <div className="flex h-full">
      {wide ? (
        <aside
          aria-label="Feed filters"
          className="w-[var(--filter-width)] shrink-0 overflow-y-auto border-r border-ink-500/40"
        >
          {panel}
        </aside>
      ) : null}

      <section aria-label="Insight feed" className="flex min-w-0 flex-1 flex-col">
        <FeedStatsHeader
          stats={stats.data}
          loading={stats.isLoading}
          activeRouting={filters.routing}
          onToggleRouting={toggleRouting}
        />

        {!wide ? (
          <div className="flex items-center justify-between border-b border-ink-500/40 px-5 py-2">
            <Button variant="secondary" size="sm" onClick={() => setFiltersOpen(true)}>
              <FilterIcon />
              Filters{count > 0 ? ` (${count})` : ''}
            </Button>
            {count > 0 ? (
              <Button variant="quiet" size="sm" onClick={clear}>
                Clear all
              </Button>
            ) : null}
          </div>
        ) : null}

        {showBanner ? (
          <div className="px-5 pt-3">
            <Banner onDismiss={dismissNemotronBanner}>
              The Nemotron upstream is unavailable. The feed is unaffected: escalated insights are
              marked as reviewed classically until it recovers.
            </Banner>
          </div>
        ) : null}

        {insights.isError && rows.length === 0 ? (
          <ErrorState
            error={insights.error}
            onRetry={() => void insights.refetch()}
            title="The feed did not load"
          />
        ) : insights.isLoading ? (
          <div className="flex flex-col" aria-busy="true">
            {Array.from({ length: 6 }, (_, i) => (
              <div key={i} className="border-b border-ink-500/40 py-3 pl-5 pr-4">
                <Skeleton className="h-[84px] w-full" />
              </div>
            ))}
          </div>
        ) : rows.length === 0 ? (
          count > 0 ? (
            <EmptyState
              title="No insights match these filters"
              action={
                <Button variant="secondary" onClick={clear}>
                  Clear all filters
                </Button>
              }
            >
              Loosen a filter or clear them to see the whole feed.
            </EmptyState>
          ) : (
            <EmptyState
              title="No insights yet"
              action={
                <LinkButton to="/app/ingest" variant="primary">
                  Load a demo scenario
                </LinkButton>
              }
            >
              Ingest some documents and every claim we extract will appear here, each tied to the
              sentence it came from.
            </EmptyState>
          )
        ) : (
          <InsightList
            rows={rows}
            selectedId={sheet.insightId}
            onOpen={sheet.open}
            hasNextPage={insights.hasNextPage}
            isFetchingNextPage={insights.isFetchingNextPage}
            fetchNextPage={fetchNext}
            nemotronDown={upstream.nemotronDown}
          />
        )}
      </section>

      <InsightSheet insightId={sheet.insightId} onClose={sheet.close} />

      {!wide ? (
        <Sheet
          open={filtersOpen}
          onClose={() => setFiltersOpen(false)}
          labelledBy="feed-filters-sheet-title"
        >
          <div className="flex items-center justify-between border-b border-ink-500/40 px-4 py-2">
            <h2 id="feed-filters-sheet-title" className="text-body font-semibold text-ink-50">
              Feed filters
            </h2>
            <IconButton label="Close filters" onClick={() => setFiltersOpen(false)}>
              <CloseIcon />
            </IconButton>
          </div>
          {panel}
        </Sheet>
      ) : null}
    </div>
  );
}
