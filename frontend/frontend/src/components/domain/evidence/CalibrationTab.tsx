import { useMemo, useRef, useState } from 'react';
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, XAxis, YAxis } from 'recharts';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api/endpoints';
import { isApiError, retryAfterSeconds } from '@/api/errors';
import { queryKeys, useCalibrationEval } from '@/api/queries';
import type { CalibrationBin, CalibrationMetrics, RecalibrateResponse } from '@/api/types';
import { useAuth } from '@/auth/useAuth';
import { Button } from '@/components/primitives/Button';
import { CountUp } from '@/components/primitives/CountUp';
import { Disclosure } from '@/components/primitives/Disclosure';
import { EmptyState } from '@/components/primitives/EmptyState';
import { ErrorState } from '@/components/primitives/ErrorState';
import { Skeleton } from '@/components/primitives/Skeleton';
import { useCooldown } from '@/hooks/useCooldown';
import { useElapsedSeconds } from '@/hooks/useElapsedSeconds';
import { improvementSentence } from '@/lib/calibration';
import { formatScore, formatSigned } from '@/lib/format';
import { uuid } from '@/lib/id';
import { AXIS_LINE, AXIS_TICK, GRID_STROKE, axisLabel } from './chartTheme';

export function CalibrationTab() {
  const { can } = useAuth();
  const query = useCalibrationEval();
  const [result, setResult] = useState<RecalibrateResponse | null>(null);
  const [pending, setPending] = useState(false);

  const baseline = query.data?.snapshots.find((s) => s.label === 'baseline');
  const post = query.data?.snapshots.find((s) => s.label === 'post_recalibration');

  if (query.isError && !query.data) {
    return <ErrorState error={query.error} onRetry={() => void query.refetch()} title="The calibration results did not load" />;
  }
  if (!query.data) {
    return (
      <div className="flex flex-col gap-8" aria-busy="true">
        <Skeleton className="h-24 w-full max-w-xl" />
        <Skeleton className="h-[420px] w-full" />
      </div>
    );
  }
  if (!baseline && !result) {
    return <EmptyState title="No calibration snapshot yet">Run the calibration script on the backend to create the baseline.</EmptyState>;
  }

  // What the page shows: the fresh recalibration if there is one, otherwise what the server has stored.
  const before: CalibrationMetrics | undefined = result?.before ?? baseline;
  const after: CalibrationMetrics | undefined = result?.after ?? post;
  const hardCases = result?.n_hard_negatives ?? query.data.hard_negatives_logged;

  return (
    <div className="flex flex-col gap-8">
      <div className="flex flex-col gap-6 lg:flex-row lg:items-start lg:justify-between">
        {before ? <EceHeadline before={before} after={after} result={result} /> : null}
        {can('calibration:run') ? (
          <RecalibrateControl
            hardCases={hardCases}
            onPending={setPending}
            onResult={setResult}
            result={result}
          />
        ) : null}
      </div>

      {before ? (
        <div className="grid gap-8 xl:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
          <ReliabilityDiagram before={before.bins} after={after?.bins ?? null} dimmed={pending} replayKey={result?.snapshot_id ?? null} />
          <MetricsTable before={before} after={after} />
        </div>
      ) : null}
    </div>
  );
}

// ── headline number ────────────────────────────────────────────────────────

function EceHeadline({
  before,
  after,
  result,
}: {
  before: CalibrationMetrics;
  after: CalibrationMetrics | undefined;
  result: RecalibrateResponse | null;
}) {
  const target = after ? after.ece : before.ece;
  return (
    <section aria-labelledby="ece-headline" className="flex flex-col gap-2">
      <h2 id="ece-headline" className="text-body-sm font-medium text-ink-200">
        Expected calibration error
      </h2>
      <p className="flex items-baseline gap-4 text-ink-50">
        <span className="text-display-1">
          <CountUp value={target} from={before.ece} duration={900} replayKey={result?.snapshot_id ?? null} format={(v) => v.toFixed(3)} />
        </span>
        {after ? (
          <span className="nums text-body text-ink-200">was {before.ece.toFixed(3)}</span>
        ) : null}
      </p>
      {after ? (
        <p className="nums font-mono text-body-sm text-ink-50">
          T = {before.temperature.toFixed(2)} &rarr; {after.temperature.toFixed(2)}
        </p>
      ) : (
        <p className="max-w-prose text-body-sm text-ink-200">
          This is the baseline. Recalibrating fits a temperature that pulls stated confidence toward how often the model is
          actually right.
        </p>
      )}
      <div aria-live="polite">
        {result ? (
          <p className="max-w-prose text-body text-ink-50">
            {improvementSentence(result.improvement.ece_relative, result.n_hard_negatives)}
          </p>
        ) : null}
      </div>
    </section>
  );
}

// ── recalibrate control ────────────────────────────────────────────────────

function RecalibrateControl({
  hardCases,
  onPending,
  onResult,
  result,
}: {
  hardCases: number;
  onPending: (pending: boolean) => void;
  onResult: (result: RecalibrateResponse) => void;
  result: RecalibrateResponse | null;
}) {
  const queryClient = useQueryClient();
  const cooldown = useCooldown();
  // A key generated when the control mounts. A double-click sends the same key,
  // so the server returns the same snapshot instead of running twice.
  const keyRef = useRef(uuid());
  const inFlight = useRef(false);

  const mutation = useMutation({
    mutationFn: () => api.calibration.recalibrate(keyRef.current),
    onMutate: () => onPending(true),
    onSuccess: (response) => {
      keyRef.current = uuid(); // the next deliberate press is a new run
      onResult(response);
      void queryClient.invalidateQueries({ queryKey: queryKeys.calibration() });
    },
    onError: (error) => {
      const seconds = retryAfterSeconds(error);
      if (seconds !== null) cooldown.start(seconds);
    },
    onSettled: () => {
      inFlight.current = false;
      onPending(false);
    },
  });

  const elapsed = useElapsedSeconds(mutation.isPending);
  const slow = mutation.isPending && elapsed >= 6;
  // Not a spinner: an easing bar that approaches but never claims to be finished.
  const progress = Math.min(0.95, 1 - Math.exp(-elapsed / 8));

  const press = () => {
    if (inFlight.current) return; // a second click in the same tick
    inFlight.current = true;
    mutation.mutate();
  };

  return (
    <section aria-labelledby="recalibrate" className="flex max-w-sm flex-col gap-2">
      <h2 id="recalibrate" className="text-body font-semibold text-ink-50">
        Recalibrate
      </h2>
      <p className="text-body-sm text-ink-200">
        Refits the confidence temperature using {hardCases} logged hard {hardCases === 1 ? 'case' : 'cases'}.
      </p>
      <Button
        variant="primary"
        className="self-start"
        pending={mutation.isPending}
        pendingLabel="Recalibrating…"
        disabled={cooldown.active}
        onClick={press}
      >
        {cooldown.active ? `Try again in ${cooldown.remaining}s` : result ? 'Recalibrate again' : 'Recalibrate now'}
      </Button>
      {slow ? (
        <div className="flex flex-col gap-1">
          <div
            role="progressbar"
            aria-label="Recalibration progress"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(progress * 100)}
            className="h-1.5 w-full overflow-hidden rounded-input bg-ink-500"
          >
            <div className="h-full bg-verify transition-[width] duration-quick ease-out" style={{ width: `${progress * 100}%` }} />
          </div>
          <p className="nums text-body-sm text-ink-200">Still fitting, {elapsed}s so far.</p>
        </div>
      ) : null}
      {mutation.isError ? (
        <div role="alert" className="text-body-sm text-ink-50">
          <p>{isApiError(mutation.error) ? mutation.error.message : 'Recalibration did not run.'}</p>
          {isApiError(mutation.error) && mutation.error.requestId ? (
            <p className="text-ink-200">
              Request id <span className="select-all font-mono text-ink-50">{mutation.error.requestId}</span>
            </p>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

// ── reliability diagram ────────────────────────────────────────────────────

interface BinPoint extends CalibrationBin {
  r: number;
}

const dotRadius = (count: number, max: number) => 3 + 6 * Math.sqrt(count / Math.max(max, 1));

function ReliabilityDiagram({
  before,
  after,
  dimmed,
  replayKey,
}: {
  before: readonly CalibrationBin[];
  after: readonly CalibrationBin[] | null;
  dimmed: boolean;
  replayKey: string | null;
}) {
  const maxCount = useMemo(() => Math.max(1, ...before.map((b) => b.count), ...(after ?? []).map((b) => b.count)), [before, after]);
  const withRadius = (bins: readonly CalibrationBin[]): BinPoint[] =>
    bins.filter((b) => b.count > 0).map((b) => ({ ...b, r: dotRadius(b.count, maxCount) }));
  const beforePoints = useMemo(() => withRadius(before), [before, maxCount]); // eslint-disable-line react-hooks/exhaustive-deps
  const afterPoints = useMemo(() => (after ? withRadius(after) : null), [after, maxCount]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <section aria-labelledby="reliability" className="flex flex-col gap-3">
      <h2 id="reliability" className="text-h3 text-ink-50">
        Stated confidence against how often it is right
      </h2>
      <div className="panel relative p-4">
        {dimmed ? (
          <p role="status" className="absolute left-1/2 top-1/2 z-10 -translate-x-1/2 -translate-y-1/2 text-body font-medium text-ink-50">
            Recalibrating…
          </p>
        ) : null}
        <div
          className="transition-opacity duration-quick ease-out"
          style={{ opacity: dimmed ? 0.4 : 1 }}
          role="img"
          aria-label="Reliability diagram. Average stated confidence on the horizontal axis, observed accuracy on the vertical axis. A perfectly calibrated model lies on the diagonal. The same bins are in the table below."
        >
          <ResponsiveContainer width="100%" height={400}>
            <LineChart margin={{ top: 12, right: 24, bottom: 40, left: 16 }}>
              <CartesianGrid stroke={GRID_STROKE} strokeOpacity={0.4} />
              <XAxis type="number" dataKey="avg_conf" domain={[0, 1]} tick={AXIS_TICK} stroke={AXIS_LINE} label={axisLabel('average stated confidence', 'insideBottom', { offset: -24 })} />
              <YAxis type="number" domain={[0, 1]} tick={AXIS_TICK} stroke={AXIS_LINE} label={axisLabel('observed accuracy', 'insideLeft', { angle: -90, style: { textAnchor: 'middle' } })} />
              <ReferenceLine segment={[{ x: 0, y: 0 }, { x: 1, y: 1 }]} stroke="var(--ink-500)" strokeWidth={1.5} strokeDasharray="8 6" />
              <Line
                data={beforePoints}
                dataKey="accuracy"
                type="linear"
                stroke="var(--ink-200)"
                strokeWidth={1.5}
                strokeDasharray="2 5"
                isAnimationActive={false}
                dot={(props: { cx?: number; cy?: number; payload?: BinPoint }) =>
                  props.cx === undefined || props.cy === undefined || !props.payload ? (
                    <g />
                  ) : (
                    <circle cx={props.cx} cy={props.cy} r={props.payload.r} fill="var(--ink-900)" stroke="var(--ink-200)" strokeWidth={1.5} />
                  )
                }
              />
              {afterPoints && !dimmed ? (
                <Line
                  key={replayKey ?? 'stored'}
                  data={afterPoints}
                  dataKey="accuracy"
                  type="linear"
                  stroke="var(--ink-050)"
                  strokeWidth={2}
                  isAnimationActive
                  animationDuration={900}
                  animationEasing="ease-out"
                  dot={(props: { cx?: number; cy?: number; payload?: BinPoint }) =>
                    props.cx === undefined || props.cy === undefined || !props.payload ? (
                      <g />
                    ) : (
                      <rect
                        x={props.cx - props.payload.r}
                        y={props.cy - props.payload.r}
                        width={props.payload.r * 2}
                        height={props.payload.r * 2}
                        transform={`rotate(45 ${props.cx} ${props.cy})`}
                        fill="var(--ink-050)"
                      />
                    )
                  }
                />
              ) : null}
            </LineChart>
          </ResponsiveContainer>
        </div>

        <ul className="mt-2 flex flex-wrap gap-x-6 gap-y-1 text-body-sm text-ink-200">
          <li className="flex items-center gap-2">
            <svg width="30" height="10" aria-hidden="true">
              <line x1="0" y1="5" x2="30" y2="5" stroke="var(--ink-200)" strokeWidth="1.5" strokeDasharray="2 5" />
              <circle cx="15" cy="5" r="3.5" fill="var(--ink-900)" stroke="var(--ink-200)" strokeWidth="1.5" />
            </svg>
            Before recalibration (dotted line, hollow circles)
          </li>
          <li className="flex items-center gap-2">
            <svg width="30" height="10" aria-hidden="true">
              <line x1="0" y1="5" x2="30" y2="5" stroke="var(--ink-050)" strokeWidth="2" />
              <rect x="11.5" y="1.5" width="7" height="7" transform="rotate(45 15 5)" fill="var(--ink-050)" />
            </svg>
            After recalibration (solid line, filled diamonds)
          </li>
          <li className="flex items-center gap-2">
            <svg width="30" height="10" aria-hidden="true">
              <line x1="0" y1="5" x2="30" y2="5" stroke="var(--ink-500)" strokeWidth="1.5" strokeDasharray="8 6" />
            </svg>
            Perfect calibration
          </li>
          <li>Larger marks are bins with more predictions.</li>
        </ul>
      </div>

      <Disclosure summary="Show the reliability bins as a table">
        <BinsTable before={before} after={after} />
      </Disclosure>
    </section>
  );
}

function BinsTable({ before, after }: { before: readonly CalibrationBin[]; after: readonly CalibrationBin[] | null }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-body-sm">
        <caption className="sr-only">Reliability bins before and after recalibration</caption>
        <thead className="text-ink-200">
          <tr>
            <th scope="col" className="py-1 pr-3 font-medium">Confidence bin</th>
            <th scope="col" className="py-1 pr-3 text-right font-medium">Predictions</th>
            <th scope="col" className="py-1 pr-3 text-right font-medium">Accuracy before</th>
            <th scope="col" className="py-1 text-right font-medium">Accuracy after</th>
          </tr>
        </thead>
        <tbody>
          {before.map((bin, i) => {
            const post = after?.[i];
            return (
              <tr key={bin.bin_lo} className="border-t border-ink-500/30">
                <td className="nums py-1 pr-3 text-ink-50">
                  {formatScore(bin.bin_lo)} to {formatScore(bin.bin_hi)}
                </td>
                <td className="nums py-1 pr-3 text-right text-ink-50">{bin.count}</td>
                <td className="nums py-1 pr-3 text-right text-ink-50">{bin.count ? formatScore(bin.accuracy) : 'no data'}</td>
                <td className="nums py-1 text-right text-ink-50">
                  {post ? (post.count ? formatScore(post.accuracy) : 'no data') : 'not run'}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ── before/after metrics ───────────────────────────────────────────────────

function MetricsTable({ before, after }: { before: CalibrationMetrics; after: CalibrationMetrics | undefined }) {
  const rows: { key: 'ece' | 'mce' | 'brier'; label: string; help: string }[] = [
    { key: 'ece', label: 'ECE', help: 'Expected calibration error: the average gap between stated confidence and accuracy.' },
    { key: 'mce', label: 'MCE', help: 'Maximum calibration error: the worst single bin.' },
    { key: 'brier', label: 'Brier score', help: 'Mean squared error of the probabilities. Lower is better.' },
  ];
  return (
    <section aria-labelledby="calibration-metrics" className="flex flex-col gap-3">
      <h2 id="calibration-metrics" className="text-h3 text-ink-50">
        Before and after
      </h2>
      <table className="w-full text-left text-body-sm">
        <caption className="sr-only">Calibration metrics before and after recalibration. Lower is better for all three.</caption>
        <thead className="text-ink-200">
          <tr>
            <th scope="col" className="py-2 pr-3 font-medium">Metric</th>
            <th scope="col" className="py-2 pr-3 text-right font-medium">Before</th>
            <th scope="col" className="py-2 pr-3 text-right font-medium">After</th>
            <th scope="col" className="py-2 text-right font-medium">Change</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const b = before[row.key];
            const a = after?.[row.key];
            const big = row.key === 'ece';
            return (
              <tr key={row.key} className="border-t border-ink-500/30">
                <th scope="row" className="py-2 pr-3 font-medium text-ink-50">
                  <span title={row.help}>{row.label}</span>
                </th>
                <td className={`nums py-2 pr-3 text-right text-ink-50 ${big ? 'text-h3' : ''}`}>{b.toFixed(3)}</td>
                <td className={`nums py-2 pr-3 text-right text-ink-50 ${big ? 'text-h3 font-semibold' : ''}`}>
                  {a === undefined ? <span className="text-ink-200">not run</span> : a.toFixed(3)}
                </td>
                <td className="nums py-2 text-right text-ink-200">{a === undefined ? '' : formatSigned(a - b, 3)}</td>
              </tr>
            );
          })}
          <tr className="border-t border-ink-500/30">
            <th scope="row" className="py-2 pr-3 font-medium text-ink-50">Temperature</th>
            <td className="py-2 pr-3 text-right font-mono text-ink-50">{before.temperature.toFixed(2)}</td>
            <td className="py-2 pr-3 text-right font-mono text-ink-50">
              {after ? after.temperature.toFixed(2) : <span className="font-sans text-ink-200">not run</span>}
            </td>
            <td />
          </tr>
        </tbody>
      </table>
    </section>
  );
}
