import { useEffect, useRef } from 'react';
import type { TranscriptSegment } from '@/api/types';
import { usePrefersReducedMotion } from '@/hooks/usePrefersReducedMotion';
import { cn } from '@/lib/cn';
import { formatClock } from '@/lib/format';

interface TranscriptSyncProps {
  segments: readonly TranscriptSegment[];
  activeId: string | null;
  playing: boolean;
  onSeek: (startMs: number) => void;
}

/**
 * The transcript is always visible (brief §17): it is both an accessibility
 * requirement and what makes the briefing legible in a loud room. The active
 * segment follows the audio; one that cites an insight gets a --verify left
 * border and a raised background, and that insight lights up beside it.
 * Clicking any segment seeks the audio to its start.
 */
export function TranscriptSync({ segments, activeId, playing, onSeek }: TranscriptSyncProps) {
  const listRef = useRef<HTMLOListElement>(null);
  const reduced = usePrefersReducedMotion();

  // Keep the active segment in view inside the transcript box while playing.
  useEffect(() => {
    if (!playing || !activeId) return;
    const list = listRef.current;
    const el = Array.from(list?.querySelectorAll<HTMLElement>('[data-segment-id]') ?? []).find(
      (node) => node.dataset['segmentId'] === activeId,
    );
    el?.scrollIntoView({ block: 'nearest', behavior: reduced ? 'auto' : 'smooth' });
  }, [activeId, playing, reduced]);

  return (
    <ol
      ref={listRef}
      aria-label="Briefing transcript"
      className="flex max-h-[26rem] flex-col overflow-y-auto rounded-panel border border-ink-500/40"
    >
      {segments.map((segment) => {
        const active = segment.segment_id === activeId;
        const cites = Boolean(segment.insight_id);
        return (
          <li key={segment.segment_id} data-segment-id={segment.segment_id}>
            <button
              type="button"
              aria-current={active ? 'true' : undefined}
              onClick={() => onSeek(segment.start_ms)}
              className={cn(
                'flex w-full gap-3 border-b border-l-4 border-b-ink-500/30 px-4 py-3 text-left transition-colors duration-instant ease-out focus-visible:outline-offset-[-2px]',
                active && cites ? 'border-l-verify bg-ink-700' : 'border-l-transparent',
                active && !cites && 'bg-ink-700/60',
                !active && 'hover:bg-ink-700/40',
              )}
            >
              <span className="nums w-10 shrink-0 pt-0.5 text-body-sm text-ink-200">{formatClock(segment.start_ms)}</span>
              <span className={cn('text-body', active ? 'text-ink-50' : 'text-ink-200')}>
                {segment.text}
                {cites ? <span className="sr-only"> (cites an insight)</span> : null}
              </span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}
