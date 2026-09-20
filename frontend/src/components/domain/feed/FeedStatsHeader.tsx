import type { InsightStats, RoutingBucket } from '@/api/types';
import { CountUp } from '@/components/primitives/CountUp';
import { Skeleton } from '@/components/primitives/Skeleton';
import { cn } from '@/lib/cn';
import { ROUTING_META, ROUTING_ORDER } from '@/lib/routing';

interface FeedStatsHeaderProps {
  stats: InsightStats | undefined;
  loading: boolean;
  activeRouting: readonly RoutingBucket[];
  onToggleRouting: (bucket: RoutingBucket) => void;
}

/**
 * The header strip (brief §7.2): routing counts as one proportional stacked
 * bar (click a segment to filter), the cascade fact in words, and the number
 * of documents ingested. The skeleton is the same height as the loaded strip
 * so nothing shifts when data arrives.
 */
export function FeedStatsHeader({
  stats,
  loading,
  activeRouting,
  onToggleRouting,
}: FeedStatsHeaderProps) {
  if (!stats) {
    return (
      <div className="h-[104px] border-b border-ink-500/40 px-5 py-4">
        {loading ? (
          <div className="flex flex-col gap-3">
            <Skeleton className="h-2 w-full" />
            <Skeleton className="h-5 w-2/3" />
            <Skeleton className="h-4 w-1/3" />
          </div>
        ) : null}
      </div>
    );
  }

  const counts = ROUTING_ORDER.map((bucket) => ({ bucket, count: stats.by_routing[bucket] ?? 0 }));
  const total = counts.reduce((sum, c) => sum + c.count, 0);
  const llm = stats.by_resolver.nemotron ?? 0;

  return (
    <div className="h-[104px] border-b border-ink-500/40 px-5 py-4">
      <div
        className="flex h-2 w-full gap-px overflow-hidden rounded-input bg-ink-500"
        role="group"
        aria-label="Insights by routing"
      >
        {counts
          .filter((c) => c.count > 0)
          .map(({ bucket, count }) => (
            <button
              key={bucket}
              type="button"
              aria-label={`${ROUTING_META[bucket].label}: ${count}. Filter the feed.`}
              onClick={() => onToggleRouting(bucket)}
              className="h-full transition-opacity duration-instant hover:opacity-80"
              style={{
                width: `${(count / Math.max(total, 1)) * 100}%`,
                backgroundColor: ROUTING_META[bucket].color,
              }}
            />
          ))}
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-x-5 gap-y-1">
        {counts.map(({ bucket, count }) => {
          const active = activeRouting.includes(bucket);
          return (
            <button
              key={bucket}
              type="button"
              aria-pressed={active}
              onClick={() => onToggleRouting(bucket)}
              className={cn(
                'flex items-center gap-2 rounded-input px-1 py-0.5 text-body-sm text-ink-200 hover:text-ink-50',
                active && 'bg-ink-500/50 text-ink-50',
              )}
            >
              <span
                aria-hidden="true"
                className="inline-block size-2 rounded-[1px]"
                style={{ backgroundColor: ROUTING_META[bucket].color }}
              />
              {ROUTING_META[bucket].label}
              <span className="nums font-medium text-ink-50">
                <CountUp value={count} />
              </span>
            </button>
          );
        })}
      </div>

      <p className="mt-1 flex flex-wrap items-baseline gap-x-6 gap-y-0.5 text-body text-ink-50">
        <span>
          <CountUp value={llm} /> of <CountUp value={stats.total} /> insights needed a language
          model
        </span>
        <span className="text-body-sm text-ink-200">
          <CountUp value={stats.documents_ingested} /> documents ingested
        </span>
      </p>
    </div>
  );
}
