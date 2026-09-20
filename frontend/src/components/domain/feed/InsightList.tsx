import { useCallback, useEffect, useRef, useState } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';
import type { InsightOut } from '@/api/types';
import { Button } from '@/components/primitives/Button';
import { InsightRow } from './InsightRow';

/** Past this many rows the list is virtualised. Below it, plain DOM is faster and simpler. */
const VIRTUALIZE_AFTER = 200;
const ROW_ESTIMATE = 108;

interface InsightListProps {
  rows: readonly InsightOut[];
  selectedId: string | null;
  onOpen: (id: string, opts?: { replace?: boolean }) => void;
  hasNextPage: boolean;
  isFetchingNextPage: boolean;
  fetchNextPage: () => void;
  nemotronDown: boolean;
  /** Insight highlighted by something else, such as the voice transcript. */
  highlightedId?: string | null | undefined;
}

function isEditable(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName);
}

/**
 * The scrolling list. Handles: infinite scroll (an IntersectionObserver
 * sentinel with a 400px margin), a visible "Load more" button as the keyboard
 * fallback, virtualisation past ~200 rows, and j/k/arrow navigation.
 */
export function InsightList({
  rows,
  selectedId,
  onOpen,
  hasNextPage,
  isFetchingNextPage,
  fetchNextPage,
  nemotronDown,
  highlightedId,
}: InsightListProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const sentinelRef = useRef<HTMLDivElement>(null);
  const [cursorId, setCursorId] = useState<string | null>(null);
  const virtual = rows.length > VIRTUALIZE_AFTER;

  const virtualizer = useVirtualizer({
    count: virtual ? rows.length : 0,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_ESTIMATE,
    overscan: 8,
  });

  // Infinite scroll. Re-observing after each page makes the observer report the
  // sentinel's current state again, so a tall viewport keeps filling.
  useEffect(() => {
    const sentinel = sentinelRef.current;
    const root = scrollRef.current;
    if (!sentinel || !root || !hasNextPage || isFetchingNextPage) return undefined;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) fetchNextPage();
      },
      { root, rootMargin: '400px' },
    );
    observer.observe(sentinel);
    return () => observer.disconnect();
  }, [hasNextPage, isFetchingNextPage, fetchNextPage, rows.length]);

  const focusRow = useCallback(
    (index: number, open: boolean) => {
      const row = rows[index];
      if (!row) return;
      setCursorId(row.id);
      if (virtual) virtualizer.scrollToIndex(index);
      requestAnimationFrame(() => {
        scrollRef.current?.querySelector<HTMLElement>(`[data-insight-id="${row.id}"]`)?.focus();
      });
      if (open) onOpen(row.id, { replace: true });
    },
    [rows, virtual, virtualizer, onOpen],
  );

  // j / k anywhere (outside inputs), arrows when a row has focus. While the
  // sheet is open, moving also swaps the sheet to the newly focused insight.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.metaKey || event.ctrlKey || event.altKey || isEditable(event.target)) return;
      const active = document.activeElement;
      const onRow = active instanceof HTMLElement && active.dataset['insightId'] !== undefined;
      let delta = 0;
      if (event.key === 'j') delta = 1;
      else if (event.key === 'k') delta = -1;
      else if (event.key === 'ArrowDown' && onRow) delta = 1;
      else if (event.key === 'ArrowUp' && onRow) delta = -1;
      else return;
      if (rows.length === 0) return;
      event.preventDefault();
      const currentId = selectedId ?? (onRow ? active.dataset['insightId'] : cursorId) ?? null;
      const currentIndex = currentId ? rows.findIndex((row) => row.id === currentId) : -1;
      const next = Math.min(rows.length - 1, Math.max(0, currentIndex + delta));
      focusRow(next, selectedId !== null);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [rows, selectedId, cursorId, focusRow]);

  const tabbableId = cursorId ?? selectedId ?? rows[0]?.id ?? null;
  const open = useCallback((id: string) => onOpen(id), [onOpen]);

  const renderRow = (insight: InsightOut) => (
    <InsightRow
      key={insight.id}
      insight={insight}
      selected={insight.id === selectedId}
      tabbable={insight.id === tabbableId}
      highlighted={insight.id === highlightedId}
      nemotronDown={nemotronDown}
      onOpen={open}
      onFocusRow={setCursorId}
    />
  );

  return (
    <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto" data-testid="insight-scroll">
      {virtual ? (
        <div style={{ height: virtualizer.getTotalSize(), position: 'relative' }}>
          {virtualizer.getVirtualItems().map((item) => {
            const insight = rows[item.index];
            if (!insight) return null;
            return (
              <div
                key={insight.id}
                data-index={item.index}
                ref={virtualizer.measureElement}
                style={{
                  position: 'absolute',
                  top: 0,
                  left: 0,
                  width: '100%',
                  transform: `translateY(${item.start}px)`,
                }}
              >
                {renderRow(insight)}
              </div>
            );
          })}
        </div>
      ) : (
        rows.map(renderRow)
      )}

      <div ref={sentinelRef} aria-hidden="true" className="h-px" />

      <div className="flex flex-col items-center gap-2 px-5 py-6 text-body-sm text-ink-200">
        {hasNextPage ? (
          <Button
            variant="secondary"
            pending={isFetchingNextPage}
            pendingLabel="Loading more…"
            onClick={fetchNextPage}
          >
            Load more
          </Button>
        ) : rows.length > 0 ? (
          <p>End of the feed. {rows.length} insights loaded.</p>
        ) : null}
      </div>
    </div>
  );
}
