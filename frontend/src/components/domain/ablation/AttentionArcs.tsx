import { useCallback, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import type { AttentionEdge } from '@/api/types';
import { arcGeometry } from '@/lib/arcs';
import { cn } from '@/lib/cn';

interface AttentionArcsProps {
  tokens: readonly string[];
  edges: readonly AttentionEdge[];
  /** Edges the user has marked for masking. */
  masked: ReadonlySet<string>;
  /** Edges that were just ablated: they draw as broken. */
  broken: ReadonlySet<string>;
  interactive: boolean;
  onToggle: (edgeId: string) => void;
}

const ARC_MAX = 92;
const SVG_HEIGHT = ARC_MAX + 14;
const BASE_Y = SVG_HEIGHT - 2;

interface Measured {
  centers: number[];
  width: number;
}

/**
 * The cited sentence token by token, with attention edges drawn as arcs above
 * it (brief §8). Stroke width and opacity scale with weight. Hovering an arc
 * raises it and dims the rest. Clicking (or Enter/Space) marks it for masking:
 * marked arcs are dashed in --verify.
 *
 * Index safety: `src_idx`/`dst_idx` come from the server, so an out-of-range
 * index drops that arc instead of throwing.
 */
export function AttentionArcs({
  tokens,
  edges,
  masked,
  broken,
  interactive,
  onToggle,
}: AttentionArcsProps) {
  const rowRef = useRef<HTMLDivElement>(null);
  const tokenRefs = useRef<(HTMLSpanElement | null)[]>([]);
  const [measured, setMeasured] = useState<Measured>({ centers: [], width: 0 });
  const [hoverId, setHoverId] = useState<string | null>(null);

  const measure = useCallback(() => {
    const centers = tokens.map((_, i) => {
      const el = tokenRefs.current[i];
      return el ? el.offsetLeft + el.offsetWidth / 2 : 0;
    });
    const width = rowRef.current?.scrollWidth ?? 0;
    setMeasured((prev) =>
      prev.width === width &&
      prev.centers.length === centers.length &&
      prev.centers.every((c, i) => c === centers[i])
        ? prev
        : { centers, width },
    );
  }, [tokens]);

  useLayoutEffect(() => {
    measure();
    const row = rowRef.current;
    const observer = typeof ResizeObserver === 'function' ? new ResizeObserver(measure) : null;
    if (row) observer?.observe(row);
    // Web fonts change token widths after first paint.
    void document.fonts?.ready.then(() => measure());
    return () => observer?.disconnect();
  }, [measure]);

  const maxWeight = useMemo(() => Math.max(1e-6, ...edges.map((e) => e.weight)), [edges]);

  const arcs = useMemo(() => {
    const out: {
      edge: AttentionEdge;
      d: string;
      midX: number;
      apexY: number;
      length: number;
      t: number;
    }[] = [];
    for (const edge of edges) {
      const x1 = measured.centers[edge.src_idx];
      const x2 = measured.centers[edge.dst_idx];
      if (x1 === undefined || x2 === undefined || x1 === x2) continue;
      const geometry = arcGeometry(x1, x2, BASE_Y, ARC_MAX);
      out.push({ edge, ...geometry, t: Math.min(1, Math.max(0, edge.weight / maxWeight)) });
    }
    // Raise the hovered arc by painting it last.
    return out.sort(
      (a, b) => Number(a.edge.edge_id === hoverId) - Number(b.edge.edge_id === hoverId),
    );
  }, [edges, measured, maxWeight, hoverId]);

  const onKey = (event: KeyboardEvent<SVGGElement>, id: string) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      onToggle(id);
    }
  };

  const linked = useMemo(() => {
    const set = new Set<number>();
    edges.forEach((e) => {
      set.add(e.src_idx);
      set.add(e.dst_idx);
    });
    return set;
  }, [edges]);

  return (
    <div
      className="overflow-x-auto pb-1"
      role="group"
      aria-label="Attention links between words in the cited sentence"
    >
      <div ref={rowRef} className="relative w-max min-w-full">
        <svg
          width={Math.max(measured.width, 1)}
          height={SVG_HEIGHT}
          className="block overflow-visible"
        >
          {arcs.map(({ edge, d, midX, apexY, length, t }) => {
            const isMasked = masked.has(edge.edge_id);
            const isBroken = broken.has(edge.edge_id);
            const isHover = hoverId === edge.edge_id;
            const dimmed = hoverId !== null && !isHover;
            const baseOpacity = 0.3 + 0.6 * t;
            const width = 1.25 + 4.5 * t;
            const label = `Attention from ${edge.src_token} to ${edge.dst_token}, weight ${edge.weight.toFixed(2)}`;
            return (
              <g
                key={edge.edge_id}
                role={interactive ? 'button' : 'img'}
                tabIndex={interactive ? 0 : undefined}
                aria-pressed={interactive ? isMasked : undefined}
                aria-label={
                  interactive
                    ? `${label}. ${isMasked ? 'Marked for masking.' : 'Press to mark for masking.'}`
                    : label
                }
                className={cn(interactive && 'cursor-pointer')}
                onClick={interactive ? () => onToggle(edge.edge_id) : undefined}
                onKeyDown={interactive ? (event) => onKey(event, edge.edge_id) : undefined}
                onPointerEnter={() => setHoverId(edge.edge_id)}
                onPointerLeave={() => setHoverId(null)}
                onFocus={() => setHoverId(edge.edge_id)}
                onBlur={() => setHoverId(null)}
              >
                <path d={d} fill="none" stroke="transparent" strokeWidth={16} />
                <path
                  d={d}
                  fill="none"
                  strokeLinecap="round"
                  stroke={isMasked || isBroken ? 'var(--verify)' : 'var(--ink-200)'}
                  strokeWidth={width}
                  strokeDasharray={isBroken ? length + 2 : isMasked ? '6 4' : undefined}
                  strokeDashoffset={isBroken ? length + 2 : 0}
                  style={{
                    opacity: isBroken
                      ? 0.15
                      : dimmed
                        ? baseOpacity * 0.35
                        : isMasked
                          ? 1
                          : baseOpacity,
                    transition: isBroken
                      ? 'stroke-dashoffset var(--dur-quick) var(--ease-out), opacity var(--dur-quick) var(--ease-out)'
                      : 'opacity var(--dur-instant) var(--ease-out)',
                  }}
                />
                {(isHover || isMasked) && !isBroken ? (
                  <text
                    x={midX}
                    y={apexY - 5}
                    textAnchor="middle"
                    fontSize={10}
                    fill="var(--ink-50)"
                    className="nums pointer-events-none"
                  >
                    {edge.weight.toFixed(2)}
                  </text>
                ) : null}
              </g>
            );
          })}
        </svg>
        <div className="flex w-max gap-1 text-body">
          {tokens.map((token, index) => (
            <span
              key={index}
              ref={(el) => {
                tokenRefs.current[index] = el;
              }}
              className={cn(
                'rounded-soft px-1.5 py-1 text-ink-50',
                linked.has(index) ? 'bg-ink-500/40' : 'text-ink-200',
              )}
            >
              {token}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}
