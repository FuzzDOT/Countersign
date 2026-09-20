import { useEffect, useRef } from 'react';
import { useInsight } from '@/api/queries';
import { ErrorState } from '@/components/primitives/ErrorState';
import { Skeleton } from '@/components/primitives/Skeleton';
import { usePrefersReducedMotion } from '@/hooks/usePrefersReducedMotion';
import { cn } from '@/lib/cn';
import { CitationBlock } from '../CitationBlock';
import { InsightRow } from '../feed/InsightRow';

interface BriefingInsightCardsProps {
  insightIds: readonly string[];
  activeInsightId: string | null;
  onOpen: (id: string) => void;
}

/**
 * The right-hand column. As the briefing speaks a segment that cites an
 * insight, that card lights up with its citation: the judge hears the claim
 * while watching the exact sentence it came from (brief §12.1).
 */
export function BriefingInsightCards({ insightIds, activeInsightId, onOpen }: BriefingInsightCardsProps) {
  return (
    <section aria-labelledby="briefing-insights" className="flex flex-col gap-3 lg:sticky lg:top-4 lg:self-start">
      <h3 id="briefing-insights" className="text-body font-semibold text-ink-50">
        Insights in this briefing
      </h3>
      {insightIds.length === 0 ? (
        <p className="text-body-sm text-ink-200">This briefing does not cite any insights.</p>
      ) : (
        <ul className="flex flex-col gap-3">
          {insightIds.map((id) => (
            <li key={id}>
              <Card id={id} active={id === activeInsightId} onOpen={onOpen} />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function Card({ id, active, onOpen }: { id: string; active: boolean; onOpen: (id: string) => void }) {
  const query = useInsight(id);
  const ref = useRef<HTMLDivElement>(null);
  const reduced = usePrefersReducedMotion();

  useEffect(() => {
    if (active) ref.current?.scrollIntoView({ block: 'nearest', behavior: reduced ? 'auto' : 'smooth' });
  }, [active, reduced]);

  if (query.isError) return <ErrorState compact error={query.error} onRetry={() => void query.refetch()} title="This insight did not load" />;
  if (!query.data) return <Skeleton className="h-52 w-full" />;

  return (
    <div
      ref={ref}
      className={cn(
        'overflow-hidden rounded-panel border transition-colors duration-quick ease-out',
        active ? 'border-verify bg-ink-700' : 'border-ink-500/40',
      )}
      aria-current={active ? 'true' : undefined}
    >
      <InsightRow insight={query.data} selected={false} tabbable highlighted={false} compact onOpen={onOpen} />
      <div className="p-3">
        <CitationBlock citation={query.data.citation} showTitle={false} />
      </div>
    </div>
  );
}
