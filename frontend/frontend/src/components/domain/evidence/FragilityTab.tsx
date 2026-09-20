import { useMemo } from 'react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip as ChartTooltip,
  XAxis,
  YAxis,
  ZAxis,
} from 'recharts';
import { useFragilityEval } from '@/api/queries';
import type { FragilityEval, QuartileRow, RoutingBucket } from '@/api/types';
import { Button } from '@/components/primitives/Button';
import { Disclosure } from '@/components/primitives/Disclosure';
import { ErrorState } from '@/components/primitives/ErrorState';
import { Skeleton } from '@/components/primitives/Skeleton';
import { DataTable, type Column } from '@/components/primitives/Table';
import { formatPValue, formatPercent, formatScore } from '@/lib/format';
import { insightIdFromChartClick } from '@/lib/chart';
import { ROUTING_META, ROUTING_ORDER } from '@/lib/routing';
import { fitLine } from '@/lib/trend';
import { RoutingBadge } from '../RoutingBadge';
import { AXIS_LINE, AXIS_TICK, GRID_STROKE, axisLabel, percentTick } from './chartTheme';

type MarkerShape = 'triangle' | 'diamond' | 'circle';

/** Colour is severity; the marker shape repeats it so it is never the only cue. */
const MARKERS: Record<RoutingBucket, MarkerShape> = {
  escalate_now: 'triangle',
  flag_for_review: 'diamond',
  auto_file: 'circle',
};

interface Datum {
  x: number;
  y: number;
  id: string;
}

interface FragilityTabProps {
  onOpenInsight: (id: string) => void;
}

export function FragilityTab({ onOpenInsight }: FragilityTabProps) {
  const query = useFragilityEval();
  if (query.isError && !query.data) {
    return <ErrorState error={query.error} onRetry={() => void query.refetch()} title="The fragility results did not load" />;
  }
  if (!query.data) return <FragilitySkeleton />;
  return <FragilityContent data={query.data} onOpenInsight={onOpenInsight} />;
}

function FragilitySkeleton() {
  return (
    <div className="flex flex-col gap-8" aria-busy="true">
      <Skeleton className="h-14 w-full max-w-prose" />
      <Skeleton className="h-[380px] w-full" />
      <Skeleton className="h-48 w-full" />
    </div>
  );
}

function FragilityContent({ data, onOpenInsight }: { data: FragilityEval; onOpenInsight: (id: string) => void }) {
  return (
    <div className="flex flex-col gap-10">
      {/* The sentence we want a judge to read if they read nothing else. */}
      <p className="max-w-prose text-h3 text-ink-50">{data.interpretation}</p>
      <ScatterFragility data={data} onOpenInsight={onOpenInsight} />
      <QuartileTable rows={data.quartile_table} />
      <PerturbationChart data={data} />
    </div>
  );
}

// ── scatter ────────────────────────────────────────────────────────────────

function ScatterFragility({ data, onOpenInsight }: { data: FragilityEval; onOpenInsight: (id: string) => void }) {
  const groups = useMemo(
    () =>
      ROUTING_ORDER.map((bucket) => ({
        bucket,
        points: data.scatter
          .filter((p) => p.routing === bucket)
          .map<Datum>((p) => ({ x: p.vacuity, y: p.fragility, id: p.insight_id })),
      })),
    [data.scatter],
  );

  const trend = useMemo(() => {
    const line = fitLine(data.scatter.map((p) => ({ x: p.vacuity, y: p.fragility })));
    if (!line || data.scatter.length === 0) return null;
    const xs = data.scatter.map((p) => p.vacuity);
    const x0 = Math.min(...xs);
    const x1 = Math.max(...xs);
    return { x0, y0: line.intercept + line.slope * x0, x1, y1: line.intercept + line.slope * x1 };
  }, [data.scatter]);

  const { spearman, pearson, p_value } = data.correlation;

  return (
    <section aria-labelledby="fragility-scatter" className="flex flex-col gap-3">
      <h2 id="fragility-scatter" className="text-h3 text-ink-50">
        Does uncertainty predict fragility?
      </h2>
      <div className="panel relative p-4">
        <div className="absolute left-20 top-6 z-10 rounded-input border border-ink-500/60 bg-ink-900/90 px-3 py-2">
          <p className="nums text-h1 text-ink-50">{formatScore(spearman)}</p>
          <p className="text-body-sm text-ink-200">Spearman rank correlation</p>
          <p className="nums text-body-sm text-ink-200">
            Pearson {formatScore(pearson)}, {formatPValue(p_value)}, n = {data.n_insights}
          </p>
        </div>
        <div role="img" aria-label={`Scatter plot of ${data.scatter.length} insights. Vacuity against measured fragility. Spearman correlation ${formatScore(spearman)}. The data is also available as a table below.`}>
          <ResponsiveContainer width="100%" height={400}>
            <ScatterChart margin={{ top: 16, right: 24, bottom: 40, left: 16 }}>
              <CartesianGrid stroke={GRID_STROKE} strokeOpacity={0.4} />
              <XAxis
                type="number"
                dataKey="x"
                name="vacuity"
                domain={[0, 1]}
                tick={AXIS_TICK}
                stroke={AXIS_LINE}
                label={axisLabel('epistemic uncertainty (vacuity)', 'insideBottom', { offset: -24 })}
              />
              <YAxis
                type="number"
                dataKey="y"
                name="fragility"
                domain={[0, 1]}
                tick={AXIS_TICK}
                stroke={AXIS_LINE}
                label={axisLabel('measured fragility under perturbation', 'insideLeft', {
                  angle: -90,
                  style: { textAnchor: 'middle' },
                })}
              />
              <ZAxis range={[54, 54]} />
              <ChartTooltip
                cursor={{ strokeDasharray: '3 3', stroke: 'var(--ink-200)' }}
                content={<ScatterTip />}
              />
              {groups.map((group) => (
                <Scatter
                  key={group.bucket}
                  name={ROUTING_META[group.bucket].label}
                  data={group.points}
                  fill={ROUTING_META[group.bucket].color}
                  fillOpacity={0.85}
                  shape={MARKERS[group.bucket]}
                  isAnimationActive={false}
                  cursor="pointer"
                  onClick={(point: unknown) => {
                    const id = insightIdFromChartClick(point);
                    if (id) onOpenInsight(id);
                  }}
                />
              ))}
              {trend ? (
                <ReferenceLine
                  segment={[
                    { x: trend.x0, y: trend.y0 },
                    { x: trend.x1, y: trend.y1 },
                  ]}
                  stroke="var(--ink-050)"
                  strokeWidth={1.5}
                  strokeDasharray="6 4"
                  ifOverflow="hidden"
                />
              ) : null}
            </ScatterChart>
          </ResponsiveContainer>
        </div>

        <ul className="mt-2 flex flex-wrap gap-x-6 gap-y-1 text-body-sm text-ink-200">
          {groups.map((group) => (
            <li key={group.bucket} className="flex items-center gap-2">
              <MarkerIcon shape={MARKERS[group.bucket]} color={ROUTING_META[group.bucket].color} />
              {ROUTING_META[group.bucket].label}
              <span className="nums text-ink-50">{group.points.length}</span>
            </li>
          ))}
          <li className="flex items-center gap-2">
            <svg width="22" height="8" aria-hidden="true">
              <line x1="0" y1="4" x2="22" y2="4" stroke="var(--ink-050)" strokeWidth="1.5" strokeDasharray="6 4" />
            </svg>
            Least-squares trend
          </li>
          <li>Click a point to open its insight.</li>
        </ul>
      </div>

      <Disclosure summary="Show the scatter data as a table">
        <DataTable
          caption="Vacuity and fragility for every insight"
          rows={data.scatter}
          rowKey={(p) => p.insight_id}
          maxHeight={360}
          columns={scatterColumns(onOpenInsight)}
          initialSort={{ key: 'fragility', dir: 'desc' }}
        />
      </Disclosure>
    </section>
  );
}

function scatterColumns(onOpen: (id: string) => void): Column<FragilityEval['scatter'][number]>[] {
  return [
    { key: 'routing', header: 'Routing', sortValue: (p) => ROUTING_META[p.routing].rank, render: (p) => <RoutingBadge bucket={p.routing} /> },
    { key: 'vacuity', header: 'Vacuity', align: 'right', sortValue: (p) => p.vacuity, render: (p) => formatScore(p.vacuity) },
    { key: 'fragility', header: 'Fragility', align: 'right', sortValue: (p) => p.fragility, render: (p) => formatScore(p.fragility) },
    {
      key: 'open',
      header: 'Insight',
      render: (p) => (
        <Button size="sm" variant="secondary" onClick={() => onOpen(p.insight_id)}>
          Open insight
        </Button>
      ),
    },
  ];
}

function MarkerIcon({ shape, color }: { shape: MarkerShape; color: string }) {
  return (
    <svg width="12" height="12" viewBox="-6 -6 12 12" aria-hidden="true">
      {shape === 'circle' ? (
        <circle r="4.5" fill={color} />
      ) : shape === 'diamond' ? (
        <polygon points="0,-5.5 5.5,0 0,5.5 -5.5,0" fill={color} />
      ) : (
        <polygon points="0,-5.5 5.5,4.5 -5.5,4.5" fill={color} />
      )}
    </svg>
  );
}

function ScatterTip({ active, payload }: { active?: boolean; payload?: readonly { payload?: unknown }[] }) {
  if (!active || !payload || payload.length === 0) return null;
  const datum = payload[0]?.payload as Datum | undefined;
  if (!datum) return null;
  return (
    <div className="rounded-input border border-ink-500 bg-ink-900 px-3 py-2 text-body-sm text-ink-50 shadow-overlay">
      <p className="nums">Vacuity {formatScore(datum.x)}</p>
      <p className="nums">Fragility {formatScore(datum.y)}</p>
      <p className="text-ink-200">Click to open the insight</p>
    </div>
  );
}

// ── quartile table ─────────────────────────────────────────────────────────

function QuartileTable({ rows }: { rows: readonly QuartileRow[] }) {
  const sorted = useMemo(() => [...rows].sort((a, b) => a.vacuity_quartile - b.vacuity_quartile), [rows]);
  const bottom = sorted[0];
  const top = sorted[sorted.length - 1];
  const maxFlip = Math.max(0.0001, ...sorted.map((r) => r.flip_rate));

  return (
    <section aria-labelledby="fragility-quartiles" className="flex flex-col gap-4">
      <h2 id="fragility-quartiles" className="text-h3 text-ink-50">
        Flip rate by uncertainty quartile
      </h2>

      {bottom && top && bottom !== top ? (
        <div className="grid max-w-2xl grid-cols-2 gap-6 border-y border-ink-500/40 py-4">
          <div>
            <p className="nums text-display-2 text-ink-50">{formatPercent(bottom.flip_rate)}</p>
            <p className="text-body-sm text-ink-200">of the most certain quartile change their label under perturbation</p>
          </div>
          <div>
            <p className="nums text-display-2 text-ink-50">{formatPercent(top.flip_rate)}</p>
            <p className="text-body-sm text-ink-200">of the most uncertain quartile change their label</p>
          </div>
        </div>
      ) : null}

      <div className="overflow-x-auto rounded-panel border border-ink-500/40">
        <table className="w-full text-left text-body-sm">
          <caption className="sr-only">Mean fragility and label flip rate for each vacuity quartile</caption>
          <thead className="bg-ink-700 text-ink-200">
            <tr>
              <th scope="col" className="px-3 py-2 font-medium">Quartile</th>
              <th scope="col" className="px-3 py-2 font-medium">Vacuity range</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">Mean fragility</th>
              <th scope="col" className="px-3 py-2 font-medium">Flip rate</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((row, index) => {
              const edge = index === 0 || index === sorted.length - 1;
              return (
                <tr key={row.vacuity_quartile} className="border-t border-ink-500/30">
                  <th scope="row" className="px-3 py-2 font-medium text-ink-50">
                    {row.vacuity_quartile}
                    {index === 0 ? <span className="ml-2 font-normal text-ink-200">most certain</span> : null}
                    {index === sorted.length - 1 && sorted.length > 1 ? (
                      <span className="ml-2 font-normal text-ink-200">most uncertain</span>
                    ) : null}
                  </th>
                  <td className="nums px-3 py-2 text-ink-50">
                    {formatScore(row.vacuity_range[0])} to {formatScore(row.vacuity_range[1])}
                  </td>
                  <td className="nums px-3 py-2 text-right text-ink-50">{formatScore(row.mean_fragility)}</td>
                  <td className="px-3 py-2">
                    <div className="flex items-center gap-3">
                      <span className={edge ? 'nums w-12 text-body font-semibold text-ink-50' : 'nums w-12 text-ink-50'}>
                        {formatPercent(row.flip_rate)}
                      </span>
                      <span
                        aria-hidden="true"
                        className="block h-2 flex-1 overflow-hidden rounded-input bg-ink-500"
                      >
                        <span className="block h-full bg-ink-50" style={{ width: `${(row.flip_rate / maxFlip) * 100}%` }} />
                      </span>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

// ── per-perturbation chart ─────────────────────────────────────────────────

function PerturbationChart({ data }: { data: FragilityEval }) {
  const rows = data.by_perturbation;
  return (
    <section aria-labelledby="fragility-perturbations" className="flex flex-col gap-3">
      <h2 id="fragility-perturbations" className="text-h3 text-ink-50">
        Which perturbations hurt most
      </h2>
      <div className="panel p-4">
        <div role="img" aria-label="Grouped bar chart of flip rate, mean absolute confidence change and relation loss rate for each perturbation. The same numbers are in the table below.">
          <ResponsiveContainer width="100%" height={320}>
            <BarChart data={rows} margin={{ top: 8, right: 16, bottom: 8, left: 8 }}>
              <defs>
                <pattern id="cs-hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
                  <rect width="6" height="6" fill="var(--ink-500)" />
                  <line x1="0" y1="0" x2="0" y2="6" stroke="var(--ink-050)" strokeWidth="2" />
                </pattern>
              </defs>
              <CartesianGrid stroke={GRID_STROKE} strokeOpacity={0.4} vertical={false} />
              <XAxis dataKey="perturbation" tick={AXIS_TICK} stroke={AXIS_LINE} />
              <YAxis tick={AXIS_TICK} stroke={AXIS_LINE} tickFormatter={percentTick} domain={[0, 'auto']} />
              <ChartTooltip
                cursor={{ fill: 'var(--ink-500)', fillOpacity: 0.25 }}
                contentStyle={{ background: 'var(--ink-900)', border: '1px solid var(--ink-500)', color: 'var(--ink-050)' }}
                formatter={(value) => (typeof value === 'number' ? formatPercent(value, 1) : String(value))}
              />
              <Bar dataKey="flip_rate" name="Flip rate" fill="var(--ink-050)" isAnimationActive={false} />
              <Bar dataKey="mean_abs_conf_delta" name="Mean absolute confidence change" fill="url(#cs-hatch)" isAnimationActive={false} />
              <Bar dataKey="relation_loss_rate" name="Relation loss rate" fill="var(--ink-200)" isAnimationActive={false} />
            </BarChart>
          </ResponsiveContainer>
        </div>
        <ul className="mt-2 flex flex-wrap gap-x-6 gap-y-1 text-body-sm text-ink-200">
          <li className="flex items-center gap-2"><span aria-hidden="true" className="inline-block size-3 bg-ink-50" />Flip rate</li>
          <li className="flex items-center gap-2">
            <svg width="12" height="12" aria-hidden="true">
              <defs>
                <pattern id="cs-hatch-key" width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
                  <rect width="5" height="5" fill="var(--ink-500)" />
                  <line x1="0" y1="0" x2="0" y2="5" stroke="var(--ink-050)" strokeWidth="2" />
                </pattern>
              </defs>
              <rect width="12" height="12" fill="url(#cs-hatch-key)" />
            </svg>
            Mean absolute confidence change
          </li>
          <li className="flex items-center gap-2"><span aria-hidden="true" className="inline-block size-3 bg-ink-200" />Relation loss rate</li>
        </ul>
      </div>
      <Disclosure summary="Show the perturbation results as a table">
        <DataTable
          caption="Results for each perturbation family"
          rows={rows}
          rowKey={(r) => r.perturbation}
          columns={[
            { key: 'perturbation', header: 'Perturbation', sortValue: (r) => r.perturbation, render: (r) => r.perturbation },
            { key: 'flip', header: 'Flip rate', align: 'right', sortValue: (r) => r.flip_rate, render: (r) => formatPercent(r.flip_rate, 1) },
            { key: 'delta', header: 'Mean absolute confidence change', align: 'right', sortValue: (r) => r.mean_abs_conf_delta, render: (r) => formatScore(r.mean_abs_conf_delta) },
            { key: 'loss', header: 'Relation loss rate', align: 'right', sortValue: (r) => r.relation_loss_rate, render: (r) => formatPercent(r.relation_loss_rate, 1) },
          ]}
        />
      </Disclosure>
    </section>
  );
}
