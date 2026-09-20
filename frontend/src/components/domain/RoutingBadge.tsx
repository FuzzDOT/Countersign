import { useEffect, useRef } from 'react';
import type { RoutingBucket } from '@/api/types';
import { usePrefersReducedMotion } from '@/hooks/usePrefersReducedMotion';
import { cn } from '@/lib/cn';
import { ROUTING_META, tint } from '@/lib/routing';

interface RoutingBadgeProps {
  bucket: RoutingBucket;
  /**
   * Change this to a new value to play the rubber-stamp landing: scale to 1.08
   * and settle over 240ms while the colour crossfades from the old severity to
   * the new one (the crossfade is a CSS transition on the colours).
   */
  stampKey?: string | number | null | undefined;
  /** Optional lead-in such as "Ground truth" or "Predicted". */
  prefix?: string | undefined;
  className?: string | undefined;
}

/**
 * Routing severity: colour AND a word, because colour is never the sole
 * carrier of meaning (brief §17). The only component allowed to paint a
 * --stamp-* colour as a fill.
 */
export function RoutingBadge({ bucket, stampKey, prefix, className }: RoutingBadgeProps) {
  const meta = ROUTING_META[bucket];
  const ref = useRef<HTMLSpanElement>(null);
  const reduced = usePrefersReducedMotion();
  const lastStamp = useRef(stampKey);

  useEffect(() => {
    if (stampKey === lastStamp.current) return;
    lastStamp.current = stampKey;
    if (stampKey === undefined || stampKey === null || reduced) return;
    const el = ref.current;
    if (el && typeof el.animate === 'function') {
      el.animate(
        [
          { transform: 'scale(1)' },
          { transform: 'scale(1.08)', offset: 0.4 },
          { transform: 'scale(1)' },
        ],
        { duration: 240, easing: 'cubic-bezier(0.16, 1, 0.3, 1)' },
      );
    }
  }, [stampKey, reduced]);

  return (
    <span
      ref={ref}
      data-routing={bucket}
      className={cn(
        'inline-flex items-center gap-1.5 rounded-input border px-1.5 py-0.5 text-micro font-medium text-ink-50',
        'transition-[background-color,border-color] duration-quick ease-out',
        className,
      )}
      style={{ borderColor: meta.color, backgroundColor: tint(meta.color, 22) }}
    >
      <span
        aria-hidden="true"
        className="inline-block size-2 shrink-0 rounded-[1px] transition-colors duration-quick ease-out"
        style={{ backgroundColor: meta.color }}
      />
      {prefix ? <span className="text-ink-200">{prefix}</span> : null}
      {meta.label}
    </span>
  );
}
