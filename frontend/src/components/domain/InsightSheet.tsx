import { useEffect, useRef, type RefObject } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { useInsight } from '@/api/queries';
import type { InsightDetail } from '@/api/types';
import { LinkButton } from '@/components/primitives/Button';
import { CloseIcon } from '@/components/primitives/icons';
import { IconButton } from '@/components/primitives/IconButton';
import { ErrorState } from '@/components/primitives/ErrorState';
import { Sheet } from '@/components/primitives/Sheet';
import { Skeleton } from '@/components/primitives/Skeleton';
import { formatDateTime, formatSigned } from '@/lib/format';
import { relationVerb } from '@/lib/relations';
import { AblationPanel } from './ablation/AblationPanel';
import { CitationBlock } from './CitationBlock';
import { MiniGraph } from './graph/MiniGraph';
import { RoutingBadge } from './RoutingBadge';
import { TrustPanel } from './TrustPanel';
import { ResolverMarker } from './feed/ResolverMarker';

const TITLE_ID = 'insight-sheet-title';

interface InsightSheetProps {
  insightId: string | null;
  onClose: () => void;
}

/**
 * The right-hand detail sheet. Its open state lives in the URL (see
 * useSheetControl), so a reload lands back on the same insight. The last id is
 * kept while the sheet animates out, so the content does not vanish mid-slide.
 */
export function InsightSheet({ insightId, onClose }: InsightSheetProps) {
  const lastId = useRef<string | null>(insightId);
  if (insightId) lastId.current = insightId;
  const shown = insightId ?? lastId.current;

  return (
    <Sheet open={insightId !== null} onClose={onClose} labelledBy={TITLE_ID}>
      {shown ? <SheetContent key={shown} id={shown} onClose={onClose} /> : null}
    </Sheet>
  );
}

function SectionHeading({ id, children }: { id: string; children: string }) {
  return (
    <h3 id={id} className="mb-3 text-body font-semibold text-ink-50">
      {children}
    </h3>
  );
}

function SheetContent({ id, onClose }: { id: string; onClose: () => void }) {
  const query = useInsight(id);
  const location = useLocation();
  const ablationRef = useRef<HTMLElement>(null);
  const loaded = query.data !== undefined;

  // Voice answers link here with #ablation to land on the ablation history.
  useEffect(() => {
    if (loaded && location.hash === '#ablation') {
      ablationRef.current?.scrollIntoView({ block: 'start' });
    }
  }, [loaded, location.hash]);

  return (
    <div className="flex min-h-full flex-col">
      <div className="sticky top-0 z-10 flex items-center justify-between border-b border-ink-500/40 bg-ink-700 px-6 py-3">
        <h2 id={TITLE_ID} className="text-body font-semibold text-ink-50">
          Insight detail
        </h2>
        <IconButton label="Close insight detail" onClick={onClose}>
          <CloseIcon />
        </IconButton>
      </div>

      {query.isError ? (
        <ErrorState error={query.error} onRetry={() => void query.refetch()} title="This insight did not load" />
      ) : !query.data ? (
        <SheetSkeleton />
      ) : (
        <SheetBody insight={query.data} ablationRef={ablationRef} />
      )}
    </div>
  );
}

/** Same section rhythm as the loaded sheet so nothing jumps when data lands. */
function SheetSkeleton() {
  return (
    <div className="flex flex-col gap-8 p-6" aria-busy="true">
      <Skeleton className="h-16 w-full" />
      <Skeleton className="h-32 w-full" />
      <Skeleton className="h-28 w-full" />
      <Skeleton className="h-20 w-full" />
      <Skeleton className="h-60 w-full" />
    </div>
  );
}

function SheetBody({ insight, ablationRef }: { insight: InsightDetail; ablationRef: RefObject<HTMLElement> }) {
  const nemotron = insight.nemotron;
  // So the reader's back control returns to whichever screen opened this sheet.
  const { pathname } = useLocation();
  const from = pathname.startsWith('/app/graph')
    ? 'graph'
    : pathname.startsWith('/app/feed')
      ? 'feed'
      : 'previous';
  return (
    <div className="flex flex-col gap-8 p-6">
      {/* 1. Claim */}
      <section aria-label="Claim">
        <p className="text-h2 text-ink-50">
          <EntityLink id={insight.subject.id} name={insight.subject.canonical} />
          <span className="mx-2 text-body font-normal text-ink-200">{relationVerb(insight.relation)}</span>
          <EntityLink id={insight.object.id} name={insight.object.canonical} />
        </p>
        <p className="mt-1 text-body-sm text-ink-200">Extracted {formatDateTime(insight.created_at)}</p>
      </section>

      {/* 2. Trust */}
      <section aria-labelledby="sheet-trust">
        <SectionHeading id="sheet-trust">Trust</SectionHeading>
        <TrustPanel trust={insight.trust} />
      </section>

      {/* 3. Source */}
      <section aria-labelledby="sheet-source">
        <SectionHeading id="sheet-source">Source</SectionHeading>
        <CitationBlock
          citation={insight.citation}
          action={
            <LinkButton
              variant="primary"
              state={{ from }}
              to={`/app/document/${insight.citation.document_id}?span=${encodeURIComponent(insight.id)}`}
            >
              Open full document
            </LinkButton>
          }
        />
      </section>

      {/* 4. Routing */}
      <section aria-labelledby="sheet-routing">
        <SectionHeading id="sheet-routing">Routing</SectionHeading>
        <div className="flex flex-wrap items-center gap-3">
          <RoutingBadge bucket={insight.routing} />
          <span className="text-body-sm text-ink-200">
            {insight.resolved_by === 'nemotron' ? 'Decided by Nemotron' : 'Decided by the classical gate'}
          </span>
          <ResolverMarker insight={insight} />
        </div>
        {insight.degraded ? (
          <p className="mt-2 max-w-prose text-body-sm text-ink-200">
            Nemotron was unavailable for this insight, so it was reviewed classically and the classical
            decision stands.
          </p>
        ) : null}
        {nemotron ? (
          <figure className="mt-3">
            <figcaption className="mb-1 text-body-sm text-ink-200">
              Nemotron&rsquo;s stated reasoning. This is the model&rsquo;s explanation, not a verified fact.
            </figcaption>
            <blockquote className="max-w-prose border-l-2 border-ink-500 pl-4 text-body text-ink-50">
              {nemotron.rationale}
            </blockquote>
            <p className="mt-2 text-body-sm text-ink-200">
              Latency <span className="font-mono text-ink-50">{nemotron.latency_ms}ms</span>
            </p>
          </figure>
        ) : null}
      </section>

      {/* 5. Attention and ablation */}
      {insight.attention_available ? (
        <section id="ablation" ref={ablationRef} aria-labelledby="sheet-ablation" className="scroll-mt-16">
          <SectionHeading id="sheet-ablation">Attention and ablation</SectionHeading>
          <AblationPanel insight={insight} />
        </section>
      ) : null}

      {/* 6. Fragility trials */}
      <section aria-labelledby="sheet-fragility">
        <SectionHeading id="sheet-fragility">Fragility trials</SectionHeading>
        {insight.fragility_trials.length === 0 ? (
          <p className="text-body-sm text-ink-200">
            The fuzzer has not run against this insight yet, so its fragility is not yet measured.
          </p>
        ) : (
          <table className="w-full text-body-sm">
            <caption className="sr-only">Perturbation trials for this insight</caption>
            <thead>
              <tr className="border-b border-ink-500/40 text-left text-ink-200">
                <th scope="col" className="py-1.5 pr-3 font-medium">
                  Perturbation
                </th>
                <th scope="col" className="py-1.5 pr-3 font-medium">
                  Label flipped
                </th>
                <th scope="col" className="py-1.5 text-right font-medium">
                  Confidence change
                </th>
              </tr>
            </thead>
            <tbody>
              {insight.fragility_trials.map((trial) => (
                <tr key={trial.perturbation} className="border-b border-ink-500/20 last:border-0">
                  <td className="py-1.5 pr-3 text-ink-50">{trial.perturbation}</td>
                  <td className="py-1.5 pr-3 text-ink-50">{trial.label_flipped ? 'Yes' : 'No'}</td>
                  <td className="nums py-1.5 text-right text-ink-50">{formatSigned(trial.conf_delta)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {/* 7. Graph context */}
      <section aria-labelledby="sheet-graph">
        <SectionHeading id="sheet-graph">Graph context</SectionHeading>
        <MiniGraph
          subjectId={insight.subject.id}
          subjectName={insight.subject.canonical}
          neighborhood={insight.graph_neighborhood}
        />
      </section>
    </div>
  );
}

function EntityLink({ id, name }: { id: string; name: string }) {
  return (
    <Link to={`/app/graph/${id}`} className="underline decoration-ink-500 underline-offset-4 hover:decoration-ink-50">
      {name}
    </Link>
  );
}
