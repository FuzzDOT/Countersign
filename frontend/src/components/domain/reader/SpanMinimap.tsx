import { ROUTING_META } from '@/lib/routing';
import { relationLabel } from '@/lib/relations';
import type { RoutingBucket } from '@/api/types';

export interface SpanTick {
  id: string;
  /** px from the top of the text block. */
  top: number;
  routing: RoutingBucket;
  relation: string;
}

interface SpanMinimapProps {
  ticks: readonly SpanTick[];
  onSelect: (id: string) => void;
}

/**
 * A tick per citation in the left margin, at the vertical position of its
 * text, coloured by routing. Clicking a tick scrolls to that span. It is the
 * document's minimap: you can see where the trouble is before you read.
 * The colour is backed by an accessible name that includes the routing word.
 */
export function SpanMinimap({ ticks, onSelect }: SpanMinimapProps) {
  return (
    <div
      className="absolute -left-6 top-0 h-full w-5"
      role="group"
      aria-label="Citation positions in this document"
    >
      {ticks.map((tick) => (
        <button
          key={tick.id}
          type="button"
          aria-label={`Go to ${relationLabel(tick.relation)} citation, ${ROUTING_META[tick.routing].label}`}
          title={`${relationLabel(tick.relation)}, ${ROUTING_META[tick.routing].short}`}
          onClick={() => onSelect(tick.id)}
          className="absolute left-0 flex h-4 w-5 items-center"
          style={{ top: tick.top + 4 }}
        >
          <span
            className="block h-1 w-full rounded-[1px]"
            style={{ backgroundColor: ROUTING_META[tick.routing].color }}
          />
        </button>
      ))}
    </div>
  );
}
