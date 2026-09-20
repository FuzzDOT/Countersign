import { memo } from 'react';
import type { InsightOut } from '@/api/types';
import { formatDateShort } from '@/lib/format';
import { relationVerb } from '@/lib/relations';
import { ROUTING_META } from '@/lib/routing';
import { cn } from '@/lib/cn';
import { ConfidenceMeter, VacuityMeter } from '../ConfidenceMeter';
import { resolverMarker } from '@/lib/resolver';
import { ResolverMarker } from './ResolverMarker';

interface InsightRowProps {
  insight: InsightOut;
  selected: boolean;
  /** Roving tabindex: exactly one row is a tab stop. */
  tabbable: boolean;
  /** Feed-highlight from the voice transcript. */
  highlighted?: boolean | undefined;
  nemotronDown?: boolean | undefined;
  /** Stack the meters under the text. For narrow columns such as the voice console's. */
  compact?: boolean | undefined;
  onOpen: (id: string) => void;
  onFocusRow?: ((id: string) => void) | undefined;
}

/**
 * One insight as a horizontal record (brief §7.3): hairline separators, no
 * shadow, no radius, 12px vertical padding, a 3px left bar in the severity
 * colour. Subject and object are the anchors; the verb is quiet between them.
 *
 * Memoised: the list re-renders on every page fetch and filter keystroke.
 */
export const InsightRow = memo(function InsightRow({
  insight,
  selected,
  tabbable,
  highlighted,
  nemotronDown,
  compact,
  onOpen,
  onFocusRow,
}: InsightRowProps) {
  const meta = ROUTING_META[insight.routing];
  const facts: string[] = [
    insight.citation.document_title,
    formatDateShort(insight.created_at),
    meta.short,
  ];
  return (
    <button
      type="button"
      data-insight-id={insight.id}
      tabIndex={tabbable ? 0 : -1}
      aria-current={selected ? 'true' : undefined}
      onClick={() => onOpen(insight.id)}
      onFocus={() => onFocusRow?.(insight.id)}
      className={cn(
        'relative block w-full border-b border-ink-500/40 py-3 pl-5 pr-4 text-left',
        'transition-colors duration-instant ease-out hover:bg-ink-700 focus-visible:outline-offset-[-2px]',
        (selected || highlighted) && 'bg-ink-700',
        highlighted && 'shadow-[inset_0_0_0_2px_var(--verify)]',
      )}
    >
      <span
        aria-hidden="true"
        className="absolute inset-y-0 left-0 w-[3px]"
        style={{ backgroundColor: meta.color }}
      />
      <span className={cn('grid gap-x-6 gap-y-2', !compact && 'md:grid-cols-[minmax(0,1fr)_auto]')}>
        <span className="min-w-0">
          <span className="block text-h3 text-ink-50">
            {insight.subject.canonical}
            <span className="mx-2 text-body-sm font-normal text-ink-200">
              {relationVerb(insight.relation)}
            </span>
            {insight.object.canonical}
          </span>
          <span className="mt-1 block truncate font-reader text-body-sm text-ink-200">
            &ldquo;{insight.citation.sentence_text}&rdquo;
          </span>
          <span className="mt-2 flex flex-wrap items-center gap-y-1 text-body-sm text-ink-200">
            {facts.map((fact, index) => (
              <span key={index} className={cn('pr-3', index > 0 && 'border-l border-ink-500 pl-3')}>
                {fact}
              </span>
            ))}
            {resolverMarker(insight, nemotronDown) ? (
              <span className="flex items-center border-l border-ink-500 pl-3">
                <ResolverMarker insight={insight} nemotronDown={nemotronDown} />
              </span>
            ) : null}
          </span>
        </span>
        <span className={cn('flex flex-col justify-center gap-1.5', !compact && 'md:items-end')}>
          <ConfidenceMeter value={insight.trust.confidence} />
          <VacuityMeter value={insight.trust.vacuity} />
        </span>
      </span>
    </button>
  );
});
