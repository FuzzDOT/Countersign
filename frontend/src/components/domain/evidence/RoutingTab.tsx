import { useState } from 'react';
import { Bar, BarChart, CartesianGrid, Cell, LabelList, ResponsiveContainer, XAxis, YAxis } from 'recharts';
import { useNemotronRuns, useRoutingEval } from '@/api/queries';
import type { DocumentedFailure, NemotronRunOut, PerClassMetrics, RoutingEval } from '@/api/types';
import { Button } from '@/components/primitives/Button';
import { ErrorState } from '@/components/primitives/ErrorState';
import { Skeleton } from '@/components/primitives/Skeleton';
import { DataTable, type Column } from '@/components/primitives/Table';
import { formatDateTime, formatPercent, formatScore } from '@/lib/format';
import { ROUTING_META, isRoutingBucket } from '@/lib/routing';
import { PaperSurface } from '../PaperSurface';
import { RoutingBadge } from '../RoutingBadge';
import { AXIS_LINE, AXIS_TICK, GRID_STROKE, percentTick } from './chartTheme';

interface RoutingTabProps {
  onOpenInsight: (id: string) => void;
}

export function RoutingTab({ onOpenInsight }: RoutingTabProps) {
  const query = useRoutingEval();
  if (query.isError && !query.data) {
    return <ErrorState error={query.error} onRetry={() => void query.refetch()} title="The routing results did not load" />;
  }
  if (!query.data) {
    return (
      <div className="flex flex-col gap-8" aria-busy="true">
        <Skeleton className="h-64 w-full max-w-xl" />
        <Skeleton className="h-40 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-12">
      <ConfusionSection data={query.data} />
      <CascadeComparison data={query.data} />
      <FailureCases failures={query.data.documented_failures} onOpenInsight={onOpenInsight} />
      <NemotronAuditTable onOpenInsight={onOpenInsight} />
    </div>
  );
}

// ── confusion matrix ───────────────────────────────────────────────────────

const perClassColumns: Column<PerClassMetrics>[] = [
  { key: 'bucket', header: 'Bucket', render: (r) => <RoutingBadge bucket={r.bucket} /> },
  { key: 'precision', header: 'Precision', align: 'right', render: (r) => formatScore(r.precision) },
  { key: 'recall', header: 'Recall', align: 'right', render: (r) => formatScore(r.recall) },
  { key: 'f1', header: 'F1', align: 'right', render: (r) => formatScore(r.f1) },
  { key: 'support', header: 'Support', align: 'right', render: (r) => r.support },
];

function ConfusionSection({ data }: { data: RoutingEval }) {
  return (
    <section aria-labelledby="routing-matrix" className="flex flex-col gap-4">
      <h2 id="routing-matrix" className="text-h3 text-ink-50">
        How well routing matches the labelled cases
      </h2>
      <div className="flex flex-wrap gap-10">
        <div>
          <p className="nums text-display-2 text-ink-50">{formatScore(data.macro_f1)}</p>
          <p className="text-body-sm text-ink-200">Macro F1 across {data.n_cases} labelled cases</p>
        </div>
        <div>
          <p className="nums text-display-2 text-ink-50">{formatPercent(data.accuracy)}</p>
          <p className="text-body-sm text-ink-200">Accuracy</p>
        </div>
      </div>
      <div className="grid gap-8 xl:grid-cols-[auto_1fr]">
        <ConfusionMatrix labels={data.confusion_matrix.labels} matrix={data.confusion_matrix.matrix} />
        <div className="min-w-0 self-start">
          <DataTable caption="Precision, recall and F1 for each routing bucket" columns={perClassColumns} rows={data.per_class} rowKey={(r) => r.bucket} />
        </div>
      </div>
    </section>
  );
}

/**
 * 3x3 heatmap as a real table. Cell fill opacity scales with the count, the
 * count sits in the middle, totals are in the gutter, and the diagonal (the
 * correct answers) is outlined in --verify. Axes are spelled out in words:
 * an unlabelled confusion matrix is a classic own-goal.
 */
function ConfusionMatrix({ labels, matrix }: { labels: readonly string[]; matrix: readonly (readonly number[])[] }) {
  const max = Math.max(1, ...matrix.flat());
  const rowTotals = matrix.map((row) => row.reduce((a, b) => a + b, 0));
  const colTotals = labels.map((_, c) => matrix.reduce((sum, row) => sum + (row[c] ?? 0), 0));
  const name = (label: string) => (isRoutingBucket(label) ? ROUTING_META[label].label : label);

  return (
    <div className="overflow-x-auto">
      <table className="border-separate border-spacing-1 text-body-sm">
        <caption className="sr-only">Confusion matrix. Rows are the ground truth bucket, columns are the predicted bucket.</caption>
        <thead>
          <tr>
            <td />
            <td />
            <th scope="colgroup" colSpan={labels.length} className="pb-1 text-center font-medium text-ink-50">
              Predicted
            </th>
            <td />
          </tr>
          <tr>
            <td />
            <td />
            {labels.map((label) => (
              <th key={label} scope="col" className="w-24 px-1 pb-1 text-center font-medium text-ink-200">
                {name(label)}
              </th>
            ))}
            <th scope="col" className="w-16 px-1 pb-1 text-center font-medium text-ink-200">
              Total
            </th>
          </tr>
        </thead>
        <tbody>
          {labels.map((label, r) => (
            <tr key={label}>
              {r === 0 ? (
                <th
                  scope="rowgroup"
                  rowSpan={labels.length}
                  className="pr-2 text-center font-medium text-ink-50"
                  style={{ writingMode: 'vertical-rl', transform: 'rotate(180deg)' }}
                >
                  Ground truth
                </th>
              ) : null}
              <th scope="row" className="pr-2 text-right font-medium text-ink-200">
                {name(label)}
              </th>
              {labels.map((column, c) => {
                const count = matrix[r]?.[c] ?? 0;
                const diagonal = r === c;
                return (
                  <td
                    key={column}
                    className="h-20 w-24 text-center align-middle"
                    style={{
                      backgroundColor: `color-mix(in srgb, var(--ink-200) ${Math.round(8 + 47 * (count / max))}%, transparent)`,
                      outline: diagonal ? '2px solid var(--verify)' : undefined,
                      outlineOffset: diagonal ? '-2px' : undefined,
                    }}
                  >
                    <span className="nums text-h3 text-ink-50">{count}</span>
                  </td>
                );
              })}
              <td className="nums text-center text-ink-200">{rowTotals[r]}</td>
            </tr>
          ))}
          <tr>
            <td />
            <th scope="row" className="pr-2 pt-1 text-right font-medium text-ink-200">
              Total
            </th>
            {colTotals.map((total, c) => (
              <td key={labels[c] ?? c} className="nums pt-1 text-center text-ink-200">
                {total}
              </td>
            ))}
            <td />
          </tr>
        </tbody>
      </table>
    </div>
  );
}

// ── cascade comparison ─────────────────────────────────────────────────────

function CascadeComparison({ data }: { data: RoutingEval }) {
  const b = data.cascade_baseline;
  const accuracy = [
    { name: 'Classical only', value: b.classical_only_accuracy, emphasis: false },
    { name: 'Cascade', value: b.cascade_accuracy, emphasis: true },
    { name: 'Nemotron on everything', value: b.nemotron_on_everything_accuracy, emphasis: false },
  ];
  const calls = [
    { name: 'Cascade', value: b.cascade_llm_calls, emphasis: true },
    { name: 'Nemotron on everything', value: b.nemotron_on_everything_llm_calls, emphasis: false },
  ];
  return (
    <section aria-labelledby="routing-cascade" className="flex flex-col gap-4">
      <h2 id="routing-cascade" className="text-h3 text-ink-50">
        Why Nemotron is only asked about the hard cases
      </h2>
      <div className="grid gap-6 lg:grid-cols-2">
        <BarPanel title="Accuracy" label="Bar chart comparing routing accuracy of classical only, the cascade, and Nemotron on everything." data={accuracy} format={(v) => formatPercent(v)} domain={[0, 1]} tick={percentTick} />
        <BarPanel title="Language model calls" label="Bar chart comparing the number of Nemotron calls made by the cascade and by running Nemotron on everything." data={calls} format={(v) => String(Math.round(v))} domain={[0, 'auto']} tick={(v) => String(Math.round(v))} />
      </div>
      <p className="max-w-prose text-body text-ink-50">{b.interpretation}</p>
    </section>
  );
}

interface BarDatum {
  name: string;
  value: number;
  emphasis: boolean;
}

function BarPanel({
  title,
  label,
  data,
  format,
  domain,
  tick,
}: {
  title: string;
  label: string;
  data: BarDatum[];
  format: (value: number) => string;
  domain: [number, number | 'auto'];
  tick: (value: number) => string;
}) {
  return (
    <div className="panel p-4">
      <h3 className="mb-2 text-body font-semibold text-ink-50">{title}</h3>
      <div role="img" aria-label={`${label} ${data.map((d) => `${d.name}: ${format(d.value)}`).join('. ')}.`}>
        <ResponsiveContainer width="100%" height={240}>
          <BarChart data={data} margin={{ top: 24, right: 8, bottom: 8, left: 0 }}>
            <CartesianGrid stroke={GRID_STROKE} strokeOpacity={0.4} vertical={false} />
            <XAxis dataKey="name" tick={AXIS_TICK} stroke={AXIS_LINE} interval={0} />
            <YAxis tick={AXIS_TICK} stroke={AXIS_LINE} domain={domain} tickFormatter={tick} />
            <Bar dataKey="value" isAnimationActive={false} maxBarSize={72}>
              {data.map((d) => (
                <Cell key={d.name} fill={d.emphasis ? 'var(--ink-050)' : 'var(--ink-500)'} stroke="var(--ink-200)" />
              ))}
              <LabelList
                dataKey="value"
                position="top"
                fill="var(--ink-050)"
                fontSize={12}
                formatter={(v: unknown) => (typeof v === 'number' ? format(v) : String(v))}
              />
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

// ── documented failures ────────────────────────────────────────────────────

function FailureCases({ failures, onOpenInsight }: { failures: readonly DocumentedFailure[]; onOpenInsight: (id: string) => void }) {
  return (
    <section aria-labelledby="routing-failures" className="flex flex-col gap-6">
      <div>
        <h2 id="routing-failures" className="text-h2 text-ink-50">
          Where it gets things wrong
        </h2>
        <p className="mt-1 max-w-prose text-body text-ink-200">
          Cases the system routed differently from the labelled answer, with our own explanation of why.
        </p>
      </div>
      {failures.length === 0 ? (
        <p className="text-body text-ink-200">No documented failures were returned.</p>
      ) : (
        failures.map((failure) => <FailureCaseCard key={failure.case_id} failure={failure} onOpenInsight={onOpenInsight} />)
      )}
    </section>
  );
}

function FailureCaseCard({ failure, onOpenInsight }: { failure: DocumentedFailure; onOpenInsight: (id: string) => void }) {
  const insightId = failure.insight_id;
  return (
    <article className="flex flex-col gap-4 border-t border-ink-500/40 pt-6">
      <PaperSurface className="px-6 py-5">
        <p className="max-w-reader whitespace-pre-wrap break-words text-reader-body">{failure.sentence_text}</p>
      </PaperSurface>
      <div className="flex flex-wrap items-center gap-3">
        <RoutingBadge bucket={failure.ground_truth} prefix="Ground truth" />
        <RoutingBadge bucket={failure.predicted} prefix="Predicted" />
        {insightId ? (
          <Button size="sm" variant="secondary" onClick={() => onOpenInsight(insightId)}>
            Open insight
          </Button>
        ) : null}
      </div>
      <p className="max-w-prose text-body text-ink-50">{failure.note}</p>
    </article>
  );
}

// ── Nemotron audit log ─────────────────────────────────────────────────────

function RationaleCell({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  const long = text.length > 90;
  return (
    <div className="max-w-md">
      <p className={open || !long ? 'whitespace-pre-wrap' : 'truncate'}>{text}</p>
      {long ? (
        <button type="button" aria-expanded={open} onClick={() => setOpen((v) => !v)} className="mt-1 text-body-sm text-ink-200 underline underline-offset-4 hover:text-ink-50">
          {open ? 'Show less' : 'Show full rationale'}
        </button>
      ) : null}
    </div>
  );
}

function NemotronAuditTable({ onOpenInsight }: { onOpenInsight: (id: string) => void }) {
  const query = useNemotronRuns();
  const rows = query.data?.pages.flatMap((page) => page.data) ?? [];

  const columns: Column<NemotronRunOut>[] = [
    { key: 'time', header: 'Time', sortValue: (r) => r.created_at, render: (r) => formatDateTime(r.created_at) },
    {
      key: 'insight',
      header: 'Insight',
      render: (r) => {
        const id = r.insight_id;
        return id ? (
          <Button size="sm" variant="secondary" onClick={() => onOpenInsight(id)}>
            Open insight
          </Button>
        ) : (
          <span className="text-ink-200">none</span>
        );
      },
    },
    { key: 'decision', header: 'Decision', sortValue: (r) => ROUTING_META[r.decision].rank, render: (r) => <RoutingBadge bucket={r.decision} /> },
    { key: 'rationale', header: 'Rationale', render: (r) => <RationaleCell text={r.rationale} /> },
    { key: 'latency', header: 'Latency', align: 'right', sortValue: (r) => r.latency_ms, render: (r) => `${r.latency_ms}ms` },
    {
      key: 'tokens',
      header: 'Tokens in and out',
      align: 'right',
      render: (r) => (r.input_tokens == null && r.output_tokens == null ? 'n/a' : `${r.input_tokens ?? '?'} and ${r.output_tokens ?? '?'}`),
    },
    {
      key: 'sha',
      header: 'Prompt SHA',
      render: (r) => (
        <span className="font-mono text-micro text-ink-200" title={r.prompt_sha}>
          {r.prompt_sha.slice(0, 12)}
        </span>
      ),
    },
    {
      key: 'degraded',
      header: 'Note',
      render: (r) => (r.degraded ? <span className="text-ink-200">classical fallback</span> : null),
    },
  ];

  return (
    <section aria-labelledby="routing-audit" className="flex flex-col gap-3">
      <div>
        <h2 id="routing-audit" className="text-h3 text-ink-50">
          Nemotron audit log
        </h2>
        <p className="mt-1 max-w-prose text-body-sm text-ink-200">
          Every call Nemotron was asked to make: what it decided, why it said so, how long it took, and a digest of
          the exact prompt.
        </p>
      </div>
      {query.isError && rows.length === 0 ? (
        <ErrorState compact error={query.error} onRetry={() => void query.refetch()} title="The audit log did not load" />
      ) : query.isLoading ? (
        <Skeleton className="h-64 w-full" />
      ) : (
        <>
          <DataTable caption="Nemotron calls" columns={columns} rows={rows} rowKey={(r) => r.id} initialSort={{ key: 'time', dir: 'desc' }} emptyText="Nemotron has not been called yet." />
          {query.hasNextPage ? (
            <Button
              variant="secondary"
              className="self-start"
              pending={query.isFetchingNextPage}
              pendingLabel="Loading more…"
              onClick={() => void query.fetchNextPage()}
            >
              Load more
            </Button>
          ) : null}
        </>
      )}
    </section>
  );
}
