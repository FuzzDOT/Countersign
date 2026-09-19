import { Link } from 'react-router-dom';
import { useEntity } from '@/api/queries';
import type { NeighborOut } from '@/api/types';
import { Button, LinkButton } from '@/components/primitives/Button';
import { CloseIcon } from '@/components/primitives/icons';
import { IconButton } from '@/components/primitives/IconButton';
import { ErrorState } from '@/components/primitives/ErrorState';
import { Skeleton } from '@/components/primitives/Skeleton';
import { formatDateShort, formatScore } from '@/lib/format';
import { entityTypeLabel, relationVerb } from '@/lib/relations';

interface EntityPanelProps {
  entityId: string;
  onClose: () => void;
  nodeHref: (id: string) => string;
  onOpenInsight: (id: string) => void;
  onShowNeighbourhood: (id: string) => void;
}

function neighbourSentence(n: NeighborOut): string {
  return n.direction === 'out'
    ? `${relationVerb(n.relation)}`
    : `${relationVerb(n.relation)} (incoming)`;
}

/** The left detail panel for a selected entity (brief §9.3). */
export function EntityPanel({ entityId, onClose, nodeHref, onOpenInsight, onShowNeighbourhood }: EntityPanelProps) {
  const query = useEntity(entityId);

  return (
    <aside aria-label="Entity details" className="flex h-full flex-col overflow-y-auto border-r border-ink-500/40 bg-ink-700">
      <div className="flex items-start justify-between gap-2 border-b border-ink-500/40 p-4">
        {query.data ? (
          <div>
            <h2 className="text-h3 text-ink-50">{query.data.entity.canonical}</h2>
            <p className="text-body-sm text-ink-200">{entityTypeLabel(query.data.entity.entity_type)}</p>
          </div>
        ) : (
          <div className="flex flex-col gap-2">
            <Skeleton className="h-6 w-40" />
            <Skeleton className="h-4 w-24" />
          </div>
        )}
        <IconButton label="Close entity details" onClick={onClose}>
          <CloseIcon />
        </IconButton>
      </div>

      {query.isError ? (
        <ErrorState compact error={query.error} onRetry={() => void query.refetch()} title="Entity did not load" />
      ) : query.data ? (
        <div className="flex flex-col gap-6 p-4">
          <dl className="grid grid-cols-2 gap-3 text-body-sm">
            <div>
              <dt className="text-ink-200">Risk</dt>
              <dd className="nums text-body text-ink-50">{formatScore(query.data.entity.risk)}</dd>
            </div>
            <div>
              <dt className="text-ink-200">First seen</dt>
              <dd className="text-body text-ink-50">{formatDateShort(query.data.entity.first_seen)}</dd>
            </div>
            <div>
              <dt className="text-ink-200">Mentions</dt>
              <dd className="nums text-body text-ink-50">{query.data.entity.mention_count}</dd>
            </div>
            <div>
              <dt className="text-ink-200">Insights</dt>
              <dd className="nums text-body text-ink-50">{query.data.insight_count}</dd>
            </div>
          </dl>

          <div className="flex flex-wrap gap-2">
            <LinkButton to={`/app/feed?entity_id=${encodeURIComponent(entityId)}`} size="sm" variant="secondary">
              Show insights in the feed
            </LinkButton>
            <Button size="sm" variant="secondary" onClick={() => onShowNeighbourhood(entityId)}>
              Show only this neighbourhood
            </Button>
          </div>

          {query.data.aliases.length > 0 ? (
            <section aria-labelledby="entity-aliases">
              <h3 id="entity-aliases" className="mb-1 text-body-sm font-medium text-ink-50">
                Also written as
              </h3>
              <ul className="text-body-sm text-ink-200">
                {query.data.aliases.map((alias) => (
                  <li key={alias}>{alias}</li>
                ))}
              </ul>
            </section>
          ) : null}

          <section aria-labelledby="entity-neighbours">
            <h3 id="entity-neighbours" className="mb-2 text-body-sm font-medium text-ink-50">
              Connected entities ({query.data.neighbors.length})
            </h3>
            <ul className="flex flex-col divide-y divide-ink-500/30">
              {query.data.neighbors.map((n, index) => {
                const first = n.insight_ids[0];
                return (
                  <li key={`${n.entity.id}-${n.relation}-${index}`} className="flex flex-col gap-1 py-2 text-body-sm">
                    <Link to={nodeHref(n.entity.id)} className="font-medium text-ink-50 underline-offset-4 hover:underline">
                      {n.entity.canonical}
                    </Link>
                    <span className="nums text-ink-200">
                      {neighbourSentence(n)}, confidence {formatScore(n.confidence)}
                    </span>
                    {first ? (
                      <Button size="sm" variant="quiet" className="self-start" onClick={() => onOpenInsight(first)}>
                        Open insight
                      </Button>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          </section>

          <section aria-labelledby="entity-documents">
            <h3 id="entity-documents" className="mb-2 text-body-sm font-medium text-ink-50">
              Documents ({query.data.documents.length})
            </h3>
            <ul className="flex flex-col gap-1 text-body-sm">
              {query.data.documents.map((doc) => (
                <li key={doc.id}>
                  <Link
                    to={`/app/document/${doc.id}`}
                    state={{ from: 'graph' }}
                    className="text-ink-50 underline-offset-4 hover:underline"
                  >
                    {doc.title}
                  </Link>
                  <span className="text-ink-200">
                    {' '}
                    ({doc.mention_count} {doc.mention_count === 1 ? 'mention' : 'mentions'})
                  </span>
                </li>
              ))}
            </ul>
          </section>
        </div>
      ) : null}
    </aside>
  );
}
