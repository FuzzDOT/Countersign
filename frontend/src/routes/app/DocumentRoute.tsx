import { useEffect, useRef, useState } from 'react';
import { useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useDocument } from '@/api/queries';
import { useAuth } from '@/auth/useAuth';
import { InsightSheet } from '@/components/domain/InsightSheet';
import { DocumentReader } from '@/components/domain/reader/DocumentReader';
import { EmptyState } from '@/components/primitives/EmptyState';
import { ErrorState } from '@/components/primitives/ErrorState';
import { Skeleton } from '@/components/primitives/Skeleton';
import { useSheetControl } from '@/hooks/useSheetControl';
import { cn } from '@/lib/cn';
import { formatDateLong } from '@/lib/format';

const SOURCE_LABELS: Record<string, string> = {
  invoice: 'Invoice',
  email: 'Email',
  press_release: 'Press release',
  rss: 'News feed item',
  gdelt: 'GDELT news event',
  note: 'Note',
  transaction_log: 'Transaction log',
};

/**
 * /app/document/:docId. The surface flip (brief §10.1): the ink background
 * crossfades to paper while the column narrows to 66ch and the body sets in
 * Literata. The rail (owned by the shell) stays ink: the document is an object
 * in the app, not a new app.
 *
 * View state is in the URL: ?span= (scroll and pulse), ?mentions=0, ?escalated=1.
 */
export default function DocumentRoute() {
  const { can } = useAuth();
  const { docId = null } = useParams<{ docId: string }>();
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const location = useLocation();
  const sheet = useSheetControl();
  const scrollRef = useRef<HTMLDivElement>(null);

  const query = useDocument(docId);
  const doc = query.data;

  // Start on ink, then flip once there is a document to show.
  const [flipped, setFlipped] = useState(false);
  useEffect(() => {
    if (!doc) return undefined;
    const frame = requestAnimationFrame(() => requestAnimationFrame(() => setFlipped(true)));
    return () => cancelAnimationFrame(frame);
  }, [doc]);

  if (!can('insights:read')) {
    return (
      <EmptyState title="You do not have access to documents">
        Ask an owner to grant access.
      </EmptyState>
    );
  }

  const showMentions = params.get('mentions') !== '0';
  const onlyEscalated = params.get('escalated') === '1';
  const focusSpanId = params.get('span');

  const setFlag = (key: string, on: boolean, defaultOn: boolean) =>
    setParams(
      (current) => {
        const next = new URLSearchParams(current);
        if (on === defaultOn) next.delete(key);
        else next.set(key, on ? '1' : '0');
        return next;
      },
      { replace: true },
    );

  const from = (location.state as { from?: string } | null)?.from;
  const canGoBack = location.key !== 'default';
  const backLabel =
    from === 'graph'
      ? 'Back to the graph'
      : from === 'feed'
        ? 'Back to the feed'
        : canGoBack
          ? 'Back'
          : 'Go to the feed';

  return (
    <div
      ref={scrollRef}
      className={cn(
        'h-full overflow-y-auto transition-[background-color,color] duration-move ease-inout',
        flipped ? 'bg-paper text-paper-text' : 'bg-ink-900 text-ink-50',
      )}
    >
      <div
        className="mx-auto px-6 py-6 transition-[max-width] duration-move ease-inout"
        style={{ maxWidth: flipped ? '66ch' : '100ch' }}
      >
        {query.isError ? (
          <ErrorState
            error={query.error}
            onRetry={() => void query.refetch()}
            title="The document did not load"
          />
        ) : !doc ? (
          <div className="flex flex-col gap-4" aria-busy="true">
            <Skeleton className="h-8 w-2/3" />
            <Skeleton className="h-4 w-1/3" />
            <Skeleton className="h-64 w-full" />
          </div>
        ) : (
          <div
            className={cn(
              'transition-opacity duration-move ease-inout',
              flipped ? 'opacity-100' : 'opacity-0',
            )}
          >
            <header className="mb-8 border-b border-paper-rule pb-4 font-sans">
              <button
                type="button"
                onClick={() => (canGoBack ? navigate(-1) : navigate('/app/feed'))}
                className="mb-3 rounded-input border border-paper-text/40 px-4 py-1.5 text-body-sm font-semibold text-paper-text hover:border-verify hover:bg-paper-rule/50"
              >
                {backLabel}
              </button>
              <h1 className="text-h2 text-paper-text">{doc.title}</h1>
              <p className="mt-1 text-body-sm text-paper-text/70">
                {SOURCE_LABELS[doc.source] ?? doc.source}, received{' '}
                {formatDateLong(doc.received_at)}
              </p>
              <div className="mt-3 flex flex-wrap gap-x-6 gap-y-2 text-body-sm">
                <PaperToggle
                  label="Show entity mentions"
                  checked={showMentions}
                  onChange={(on) => setFlag('mentions', on, true)}
                />
                <PaperToggle
                  label="Show only escalated spans"
                  checked={onlyEscalated}
                  onChange={(on) => setFlag('escalated', on, false)}
                />
              </div>
            </header>

            <DocumentReader
              doc={doc}
              showMentions={showMentions}
              onlyEscalated={onlyEscalated}
              focusSpanId={focusSpanId}
              scrollRef={scrollRef}
              onOpenInsight={(id) => sheet.open(id)}
            />
            <div className="h-[40vh]" aria-hidden="true" />
          </div>
        )}
      </div>

      <InsightSheet insightId={sheet.insightId} onClose={sheet.close} />
    </div>
  );
}

function PaperToggle({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (on: boolean) => void;
}) {
  return (
    <label className="flex cursor-pointer items-center gap-2 text-paper-text">
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        className="size-4 accent-verify"
      />
      {label}
    </label>
  );
}
