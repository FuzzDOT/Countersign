import { useMemo } from 'react';
import type { InsightStats, RoutingBucket } from '@/api/types';
import { Button } from '@/components/primitives/Button';
import { Checkbox } from '@/components/primitives/Checkbox';
import { Input } from '@/components/primitives/Input';
import { DualRangeSlider, RangeSlider } from '@/components/primitives/RangeSlider';
import { SegmentedControl } from '@/components/primitives/SegmentedControl';
import { Select } from '@/components/primitives/Select';
import { useDebouncedField } from '@/hooks/useDebouncedField';
import { SORT_OPTIONS, type FeedFilters } from '@/lib/feedFilters';
import { RELATION_TYPES, relationLabel } from '@/lib/relations';
import { ROUTING_META, ROUTING_ORDER } from '@/lib/routing';
import type { InsightSort } from '@/api/types';

interface FeedFiltersPanelProps {
  filters: FeedFilters;
  onChange: (next: FeedFilters) => void;
  onClear: () => void;
  activeCount: number;
  stats: InsightStats | undefined;
}

const RESOLVER_OPTIONS = [
  { value: 'all', label: 'All' },
  { value: 'classical', label: 'Classical' },
  { value: 'nemotron', label: 'Nemotron' },
] as const;

const sameRange = (a: [number, number], b: [number, number]) => a[0] === b[0] && a[1] === b[1];

/**
 * Every control here writes to the URL (brief §7.4). Text and slider input are
 * debounced 300ms; checkboxes and segmented controls apply immediately.
 */
export function FeedFiltersPanel({ filters, onChange, onClear, activeCount, stats }: FeedFiltersPanelProps) {
  const [q, setQ] = useDebouncedField(filters.q, (value) => onChange({ ...filters, q: value }));

  const externalRange = useMemo<[number, number]>(
    () => [filters.minConfidence ?? 0, filters.maxConfidence ?? 1],
    [filters.minConfidence, filters.maxConfidence],
  );
  const [range, setRange] = useDebouncedField<[number, number]>(
    externalRange,
    ([lo, hi]) =>
      onChange({ ...filters, minConfidence: lo <= 0 ? null : lo, maxConfidence: hi >= 1 ? null : hi }),
    sameRange,
  );

  const [vacuity, setVacuity] = useDebouncedField(filters.minVacuity ?? 0, (value) =>
    onChange({ ...filters, minVacuity: value <= 0 ? null : value }),
  );

  const toggleRouting = (bucket: RoutingBucket, on: boolean) =>
    onChange({
      ...filters,
      routing: on ? [...filters.routing, bucket] : filters.routing.filter((b) => b !== bucket),
    });

  const toggleRelation = (relation: string, on: boolean) =>
    onChange({
      ...filters,
      relation: on ? [...filters.relation, relation] : filters.relation.filter((r) => r !== relation),
    });

  return (
    <div className="flex flex-col gap-6 p-4">
      <div className="flex items-center justify-between">
        <h2 className="text-body font-semibold text-ink-50">Filters</h2>
        {activeCount > 0 ? (
          <Button variant="quiet" size="sm" onClick={onClear}>
            Clear all ({activeCount})
          </Button>
        ) : null}
      </div>

      <Input
        label="Search citations"
        type="search"
        value={q}
        onChange={(event) => setQ(event.target.value)}
        placeholder="Text in the cited sentence"
      />

      <fieldset className="flex flex-col gap-2">
        <legend className="mb-1 text-body-sm font-medium text-ink-50">Routing</legend>
        {ROUTING_ORDER.map((bucket) => (
          <Checkbox
            key={bucket}
            label={ROUTING_META[bucket].label}
            checked={filters.routing.includes(bucket)}
            count={stats?.by_routing[bucket]}
            onChange={(on) => toggleRouting(bucket, on)}
          />
        ))}
      </fieldset>

      <div className="flex flex-col gap-1">
        <span className="text-body-sm font-medium text-ink-50">Decided by</span>
        <SegmentedControl
          ariaLabel="Decided by"
          size="sm"
          options={RESOLVER_OPTIONS}
          value={filters.resolvedBy ?? 'all'}
          onChange={(value) => onChange({ ...filters, resolvedBy: value === 'all' ? null : value })}
        />
      </div>

      <DualRangeSlider label="Confidence" value={range} onChange={setRange} />
      <RangeSlider label="Minimum vacuity" value={vacuity} onChange={setVacuity} />

      <fieldset className="flex flex-col gap-2">
        <legend className="mb-1 text-body-sm font-medium text-ink-50">Relation</legend>
        {RELATION_TYPES.map((relation) => (
          <Checkbox
            key={relation}
            label={relationLabel(relation)}
            checked={filters.relation.includes(relation)}
            onChange={(on) => toggleRelation(relation, on)}
          />
        ))}
      </fieldset>

      <Select
        label="Sort by"
        options={SORT_OPTIONS}
        value={filters.sort}
        onChange={(value) => onChange({ ...filters, sort: value as InsightSort })}
      />
    </div>
  );
}
