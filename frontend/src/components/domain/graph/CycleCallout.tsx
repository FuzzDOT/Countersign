import type { GraphCycle, GraphNode } from '@/api/types';
import { Button } from '@/components/primitives/Button';
import { formatScore } from '@/lib/format';
import { relationLabel } from '@/lib/relations';

interface CycleCalloutProps {
  cycles: readonly GraphCycle[];
  nodes: readonly GraphNode[];
  focusedIndex: number | null;
  onFocus: (index: number | null) => void;
}

/**
 * Lists every cycle the server found. On the demo scenario this is the planted
 * three-entity ownership loop, and a judge should not have to hunt for it
 * (brief §9.3), so it sits at the top of the side panel with its own heading.
 */
export function CycleCallout({ cycles, nodes, focusedIndex, onFocus }: CycleCalloutProps) {
  if (cycles.length === 0) return null;
  const names = new Map(nodes.map((n) => [n.id, n.canonical]));
  return (
    <section aria-labelledby="cycle-callout" className="panel p-4">
      <h2 id="cycle-callout" className="text-body font-semibold text-ink-50">
        {cycles.length === 1
          ? 'Circular ownership found'
          : `${cycles.length} circular structures found`}
      </h2>
      <p className="mt-1 text-body-sm text-ink-200">
        Detected by the server, not estimated in your browser.
      </p>
      <ul className="mt-3 flex flex-col gap-3">
        {cycles.map((cycle, index) => {
          const focused = focusedIndex === index;
          return (
            <li
              key={index}
              className="flex flex-col gap-2 border-t border-ink-500/40 pt-3 first:border-0 first:pt-0"
            >
              <p className="text-body-sm text-ink-50">
                {cycle.node_ids.map((id) => names.get(id) ?? 'Unknown entity').join(', ')}
              </p>
              <p className="nums text-body-sm text-ink-200">
                {relationLabel(cycle.relation)}, {cycle.length} entities, risk{' '}
                {formatScore(cycle.risk)}
              </p>
              <Button
                size="sm"
                variant={focused ? 'primary' : 'secondary'}
                aria-pressed={focused}
                onClick={() => onFocus(focused ? null : index)}
                className="self-start"
              >
                {focused ? 'Show everything' : 'Focus this cycle'}
              </Button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
