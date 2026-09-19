import { useMemo } from 'react';
import type { RoutingBucket } from '@/api/types';
import { Button } from '@/components/primitives/Button';
import { Checkbox } from '@/components/primitives/Checkbox';
import { RangeSlider } from '@/components/primitives/RangeSlider';
import { SegmentedControl } from '@/components/primitives/SegmentedControl';
import { useDebouncedField } from '@/hooks/useDebouncedField';
import { EMPTY_GRAPH_FILTERS, type GraphFilters } from '@/lib/graphFilters';
import { ENTITY_TYPES, RELATION_TYPES, entityTypeLabel, relationLabel } from '@/lib/relations';
import { ROUTING_META, ROUTING_ORDER } from '@/lib/routing';

interface GraphFiltersPanelProps {
  filters: GraphFilters;
  onChange: (next: GraphFilters) => void;
  onClear: () => void;
  activeCount: number;
  rootName: string | null;
}

const DEPTH_OPTIONS = [
  { value: '1', label: '1 hop' },
  { value: '2', label: '2 hops' },
  { value: '3', label: '3 hops' },
] as const;

function toggle<T>(list: readonly T[], item: T, on: boolean): T[] {
  return on ? [...list, item] : list.filter((x) => x !== item);
}

/** Entity type, relation type, minimum confidence, routing, and neighbourhood mode (brief §9.3). */
export function GraphFiltersPanel({ filters, onChange, onClear, activeCount, rootName }: GraphFiltersPanelProps) {
  const external = useMemo(() => filters.minConfidence ?? 0, [filters.minConfidence]);
  const [minConf, setMinConf] = useDebouncedField(external, (value) =>
    onChange({ ...filters, minConfidence: value <= 0 ? null : value }),
  );

  return (
    <section aria-labelledby="graph-filters" className="panel flex flex-col gap-5 p-4">
      <div className="flex items-center justify-between">
        <h2 id="graph-filters" className="text-body font-semibold text-ink-50">
          Filters
        </h2>
        {activeCount > 0 ? (
          <Button variant="quiet" size="sm" onClick={onClear}>
            Clear all ({activeCount})
          </Button>
        ) : null}
      </div>

      {filters.rootEntityId ? (
        <div className="flex flex-col gap-2">
          <p className="text-body-sm text-ink-50">
            Showing the neighbourhood of <span className="font-semibold">{rootName ?? 'the selected entity'}</span>
          </p>
          <SegmentedControl
            ariaLabel="Neighbourhood depth"
            size="sm"
            options={DEPTH_OPTIONS}
            value={String(filters.depth) as '1' | '2' | '3'}
            onChange={(value) => onChange({ ...filters, depth: Number(value) })}
          />
          <Button
            variant="quiet"
            size="sm"
            className="self-start"
            onClick={() => onChange({ ...filters, rootEntityId: null, depth: EMPTY_GRAPH_FILTERS.depth })}
          >
            Show the whole graph
          </Button>
        </div>
      ) : null}

      <fieldset className="flex flex-col gap-2">
        <legend className="mb-1 text-body-sm font-medium text-ink-50">Entity type</legend>
        {ENTITY_TYPES.map((type) => (
          <Checkbox
            key={type}
            label={entityTypeLabel(type)}
            checked={filters.entityTypes.includes(type)}
            onChange={(on) => onChange({ ...filters, entityTypes: toggle(filters.entityTypes, type, on) })}
          />
        ))}
      </fieldset>

      <fieldset className="flex flex-col gap-2">
        <legend className="mb-1 text-body-sm font-medium text-ink-50">Relation</legend>
        {RELATION_TYPES.map((relation) => (
          <Checkbox
            key={relation}
            label={relationLabel(relation)}
            checked={filters.relations.includes(relation)}
            onChange={(on) => onChange({ ...filters, relations: toggle(filters.relations, relation, on) })}
          />
        ))}
      </fieldset>

      <fieldset className="flex flex-col gap-2">
        <legend className="mb-1 text-body-sm font-medium text-ink-50">Routing</legend>
        {ROUTING_ORDER.map((bucket: RoutingBucket) => (
          <Checkbox
            key={bucket}
            label={ROUTING_META[bucket].label}
            checked={filters.routing.includes(bucket)}
            onChange={(on) => onChange({ ...filters, routing: toggle(filters.routing, bucket, on) })}
          />
        ))}
      </fieldset>

      <RangeSlider label="Minimum confidence" value={minConf} onChange={setMinConf} />
    </section>
  );
}
