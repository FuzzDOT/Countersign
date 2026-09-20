import { Fragment, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type RefObject } from 'react';
import type { DocumentDetail, MentionOut, SpanOut } from '@/api/types';
import { usePrefersReducedMotion } from '@/hooks/usePrefersReducedMotion';
import { formatScore } from '@/lib/format';
import { entityTypeLabel, relationLabel } from '@/lib/relations';
import { ROUTING_META } from '@/lib/routing';
import { buildSegments, type Segment } from '@/lib/spans';
import { SpanMinimap, type SpanTick } from './SpanMinimap';

interface DocumentReaderProps {
  doc: DocumentDetail;
  showMentions: boolean;
  onlyEscalated: boolean;
  /** `?span=` from the URL: scroll to it at 30% of the viewport and pulse once. */
  focusSpanId: string | null;
  scrollRef: RefObject<HTMLElement>;
  onOpenInsight: (id: string) => void;
}

type Popover =
  | { kind: 'span'; ids: string[]; x: number; y: number }
  | { kind: 'mention'; index: number; x: number; y: number };

/** The most severe span wins a segment's colour; ties go to the most specific (shortest). */
function dominantSpan(ids: readonly string[], byId: ReadonlyMap<string, SpanOut>): SpanOut | undefined {
  let best: SpanOut | undefined;
  for (const id of ids) {
    const span = byId.get(id);
    if (!span) continue;
    if (!best) {
      best = span;
      continue;
    }
    const rank = ROUTING_META[span.routing].rank - ROUTING_META[best.routing].rank;
    const shorter = span.char_end - span.char_start < best.char_end - best.char_start;
    if (rank > 0 || (rank === 0 && shorter)) best = span;
  }
  return best;
}

/**
 * The paper reader body (brief §10.2).
 *
 * `raw_text` goes into text nodes only: no innerHTML, no markdown, no trimming,
 * no whitespace normalising, because every offset in the product indexes into
 * this exact string. Highlights are built by slicing it at span boundaries
 * (see lib/spans.ts), so overlapping citations split cleanly and a malformed
 * offset yields a missing highlight rather than a crash.
 */
export function DocumentReader({ doc, showMentions, onlyEscalated, focusSpanId, scrollRef, onOpenInsight }: DocumentReaderProps) {
  const reduced = usePrefersReducedMotion();
  const wrapperRef = useRef<HTMLDivElement>(null);
  const hideTimer = useRef<number | undefined>(undefined);
  const [popover, setPopover] = useState<Popover | null>(null);
  const [pulseId, setPulseId] = useState<string | null>(null);
  const [ticks, setTicks] = useState<SpanTick[]>([]);

  const spans = useMemo(
    () => (onlyEscalated ? doc.spans.filter((s) => s.routing === 'escalate_now') : doc.spans),
    [doc.spans, onlyEscalated],
  );
  const mentions = useMemo<readonly MentionOut[]>(() => (showMentions ? doc.mentions : []), [doc.mentions, showMentions]);
  const segments = useMemo(() => buildSegments(doc.raw_text, spans, mentions), [doc.raw_text, spans, mentions]);
  const spanById = useMemo(() => new Map(spans.map((s) => [s.insight_id, s])), [spans]);

  // The first segment of each span is its scroll target, tick anchor and keyboard stop.
  const firstOf = useMemo(() => {
    const map = new Map<string, number>();
    segments.forEach((seg, index) => {
      seg.spanIds.forEach((id) => {
        if (!map.has(id)) map.set(id, index);
      });
    });
    return map;
  }, [segments]);

  // ── minimap tick positions ────────────────────────────────────────────────
  const measureTicks = useCallback(() => {
    const wrapper = wrapperRef.current;
    if (!wrapper) return;
    const next: SpanTick[] = [];
    wrapper.querySelectorAll<HTMLElement>('[data-first-of]').forEach((el) => {
      const id = el.dataset['firstOf'];
      const span = id ? spanById.get(id) : undefined;
      if (id && span) next.push({ id, top: el.offsetTop, routing: span.routing, relation: span.relation });
    });
    setTicks(next);
  }, [spanById]);

  useLayoutEffect(() => {
    measureTicks();
    const wrapper = wrapperRef.current;
    const observer = typeof ResizeObserver === 'function' ? new ResizeObserver(measureTicks) : null;
    if (wrapper) observer?.observe(wrapper);
    void document.fonts?.ready.then(() => measureTicks());
    return () => observer?.disconnect();
  }, [measureTicks, segments]);

  // ── scroll to a span at 30% of the viewport, and pulse it once ───────────
  const scrollToSpan = useCallback(
    (id: string, pulse: boolean) => {
      const container = scrollRef.current;
      const target = wrapperRef.current
        ? Array.from(wrapperRef.current.querySelectorAll<HTMLElement>('[data-first-of]')).find((el) => el.dataset['firstOf'] === id)
        : undefined;
      if (!container || !target) return;
      const top =
        target.getBoundingClientRect().top -
        container.getBoundingClientRect().top +
        container.scrollTop -
        container.clientHeight * 0.3;
      container.scrollTo({ top: Math.max(0, top), behavior: reduced ? 'auto' : 'smooth' });
      if (pulse) setPulseId(id);
    },
    [scrollRef, reduced],
  );

  useEffect(() => {
    if (focusSpanId) scrollToSpan(focusSpanId, true);
    // Runs when the target span or the rendered text changes, not on every scroll.
  }, [focusSpanId, doc.id, segments, scrollToSpan]);

  // ── popover ──────────────────────────────────────────────────────────────
  const place = (clientX: number, clientY: number) => {
    const rect = wrapperRef.current?.getBoundingClientRect();
    const width = rect?.width ?? 400;
    return {
      x: Math.min(Math.max(clientX - (rect?.left ?? 0), 0), Math.max(width - 264, 0)),
      y: clientY - (rect?.top ?? 0) + 14,
    };
  };
  const show = (next: Popover) => {
    window.clearTimeout(hideTimer.current);
    setPopover(next);
  };
  const hideSoon = () => {
    window.clearTimeout(hideTimer.current);
    hideTimer.current = window.setTimeout(() => setPopover(null), 160);
  };
  useEffect(() => () => window.clearTimeout(hideTimer.current), []);

  /* Pointer and click handlers on the highlighted text are a convenience for
     mouse and touch. The keyboard path is the first segment of each span,
     which is a focusable button that opens the same insight. */
  const renderSegment = (segment: Segment, index: number) => {
    if (segment.spanIds.length === 0 && segment.mentionIdx.length === 0) {
      return <Fragment key={index}>{segment.text}</Fragment>;
    }
    const dominant = dominantSpan(segment.spanIds, spanById);
    const firstFor = segment.spanIds.filter((id) => firstOf.get(id) === index);
    const pulsing = pulseId !== null && segment.spanIds.includes(pulseId);
    const mentionIndex = segment.mentionIdx[0];
    const style: CSSProperties | undefined = dominant
      ? ({ '--span-color': ROUTING_META[dominant.routing].color } as CSSProperties)
      : undefined;
    const className = [
      dominant ? 'cite-mark' : '',
      dominant && pulsing ? 'cite-mark--pulse' : '',
      mentionIndex !== undefined ? 'mention-mark' : '',
    ]
      .filter(Boolean)
      .join(' ');

    if (!dominant) {
      return (
        <span
          key={index}
          className={className}
          onPointerEnter={(e) => mentionIndex !== undefined && show({ kind: 'mention', index: mentionIndex, ...place(e.clientX, e.clientY) })}
          onPointerLeave={hideSoon}
        >
          {segment.text}
        </span>
      );
    }

    const keyboardStop = firstFor.length > 0;
    const label = `${relationLabel(dominant.relation)} citation, confidence ${formatScore(dominant.confidence)}, ${ROUTING_META[dominant.routing].label}. Press Enter to open the insight.`;
    return (
      <span
        key={index}
        className={className}
        style={style}
        data-first-of={firstFor[0]}
        role={keyboardStop ? 'button' : undefined}
        tabIndex={keyboardStop ? 0 : undefined}
        aria-label={keyboardStop ? label : undefined}
        onAnimationEnd={() => setPulseId(null)}
        onPointerEnter={(e) => show({ kind: 'span', ids: segment.spanIds, ...place(e.clientX, e.clientY) })}
        onPointerLeave={hideSoon}
        onClick={(e) => show({ kind: 'span', ids: segment.spanIds, ...place(e.clientX, e.clientY) })}
        onFocus={(e) => {
          const r = e.currentTarget.getBoundingClientRect();
          show({ kind: 'span', ids: segment.spanIds, ...place(r.left, r.bottom - 14) });
        }}
        onBlur={hideSoon}
        onKeyDown={(e) => {
          if (keyboardStop && e.key === 'Enter') onOpenInsight(dominant.insight_id);
        }}
      >
        {segment.text}
      </span>
    );
  };

  const mentionFor = popover?.kind === 'mention' ? doc.mentions[popover.index] : undefined;

  return (
    <div ref={wrapperRef} className="relative ml-6">
      <SpanMinimap ticks={ticks} onSelect={(id) => scrollToSpan(id, true)} />

      {/* Text nodes only. Never innerHTML, never a markdown renderer. */}
      <p className="whitespace-pre-wrap break-words font-reader text-reader-body text-paper-text" data-testid="reader-text">
        {segments.map(renderSegment)}
      </p>

      {popover?.kind === 'span' ? (
        <div
          role="group"
          aria-label="Citations at this position"
          className="absolute z-20 w-64 rounded-soft border border-ctl-border bg-ink-900 p-2 font-sans text-body-sm text-ink-50 shadow-overlay"
          style={{ left: popover.x, top: popover.y }}
          onPointerEnter={() => window.clearTimeout(hideTimer.current)}
          onPointerLeave={hideSoon}
        >
          <ul className="flex flex-col gap-2">
            {popover.ids.slice(0, 3).map((id) => {
              const span = spanById.get(id);
              if (!span) return null;
              return (
                <li key={id} className="flex flex-col gap-1">
                  <span>
                    {relationLabel(span.relation)}, confidence{' '}
                    <span className="nums">{formatScore(span.confidence)}</span>
                    <span className="text-ink-200"> ({ROUTING_META[span.routing].short})</span>
                  </span>
                  <button
                    type="button"
                    className="self-start rounded-input border border-ctl-border bg-ctl-alt px-3 py-0.5 text-ctl-alt-fg hover:border-verify hover:bg-ctl-alt-hover"
                    onClick={() => onOpenInsight(id)}
                  >
                    Open insight
                  </button>
                </li>
              );
            })}
          </ul>
          {popover.ids.length > 3 ? <p className="mt-1 text-ink-200">and {popover.ids.length - 3} more overlapping</p> : null}
        </div>
      ) : null}

      {popover?.kind === 'mention' && mentionFor ? (
        <div
          role="tooltip"
          className="pointer-events-none absolute z-20 rounded-soft border border-ctl-border bg-ink-900 px-2 py-1 font-sans text-body-sm text-ink-50 shadow-overlay"
          style={{ left: popover.x, top: popover.y }}
        >
          {entityTypeLabel(mentionFor.entity_type)}, tagger confidence{' '}
          <span className="nums">{formatScore(mentionFor.tagger_conf)}</span>
        </div>
      ) : null}
    </div>
  );
}
