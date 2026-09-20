import { useMemo, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api/endpoints';
import { isApiError, retryAfterSeconds } from '@/api/errors';
import { queryKeys } from '@/api/queries';
import type { AblationHistoryItem, AblationMode, AblationResponse, InsightDetail } from '@/api/types';
import { useAuth } from '@/auth/useAuth';
import { Button } from '@/components/primitives/Button';
import { Meter } from '@/components/primitives/Meter';
import { SegmentedControl } from '@/components/primitives/SegmentedControl';
import { useCooldown } from '@/hooks/useCooldown';
import { useCountUp } from '@/hooks/useCountUp';
import { formatDateTime, formatScore, formatSigned } from '@/lib/format';
import { RoutingBadge } from '../RoutingBadge';
import { AttentionArcs } from './AttentionArcs';

const MODE_OPTIONS = [
  { value: 'zero', label: 'Zero attention' },
  { value: 'uniform', label: 'Uniform attention' },
] as const;

const VERDICT_TRUE = 'This connection is causally responsible for the flag.';
const VERDICT_FALSE =
  'Removing this connection barely changed the result \u2014 the flag does not depend on it.';

/** Server history plus runs made in this session, deduplicated, newest first. */
function mergeHistory(server: readonly AblationHistoryItem[], local: readonly AblationHistoryItem[]) {
  const seen = new Set<string>();
  const merged: AblationHistoryItem[] = [];
  for (const item of [...local, ...server]) {
    if (seen.has(item.id)) continue;
    seen.add(item.id);
    merged.push(item);
  }
  return merged.sort((a, b) => b.created_at.localeCompare(a.created_at));
}

/**
 * The causal-explainability moment (brief §8). Mark attention arcs, run the
 * ablation, and watch the numbers move: confidence and vacuity count from the
 * old value to the new one (never snap), the routing badge stamps if the
 * decision changed, and the server's templated interpretation renders as-is.
 *
 * Failure handling: nothing changes on screen until a response arrives, so an
 * error leaves the before-state intact. There is no half-ablated visual state.
 */
export function AblationPanel({ insight }: { insight: InsightDetail }) {
  const { can } = useAuth();
  const queryClient = useQueryClient();
  const cooldown = useCooldown();

  const [masked, setMasked] = useState<ReadonlySet<string>>(new Set());
  const [mode, setMode] = useState<AblationMode>('zero');
  const [result, setResult] = useState<AblationResponse | null>(null);
  const [localRuns, setLocalRuns] = useState<AblationHistoryItem[]>([]);

  const canRun = can('ablation:run');

  const mutation = useMutation({
    mutationFn: (body: { masked_edges: string[]; mode: AblationMode }) => api.ablation.run(insight.id, body),
    onSuccess: (response) => {
      setResult(response);
      setLocalRuns((runs) => [
        {
          id: response.run_id,
          masked_edges: response.masked_edges,
          confidence_before: response.before.confidence,
          confidence_after: response.after.confidence,
          load_bearing: response.load_bearing,
          created_at: new Date().toISOString(),
        },
        ...runs,
      ]);
      void queryClient.invalidateQueries({ queryKey: queryKeys.insight(insight.id) });
    },
    onError: (error) => {
      const seconds = retryAfterSeconds(error);
      if (seconds !== null) cooldown.start(seconds);
    },
  });

  const shownConfidence = result ? result.after.confidence : insight.trust.confidence;
  const shownVacuity = result ? result.after.vacuity : insight.trust.vacuity;
  const shownRouting = result ? result.after.routing : insight.routing;
  const confidence = useCountUp(shownConfidence, { duration: 600 });
  const vacuity = useCountUp(shownVacuity, { duration: 600 });

  const history = useMemo(
    () => mergeHistory(insight.ablation_history, localRuns),
    [insight.ablation_history, localRuns],
  );

  const toggle = (edgeId: string) => {
    setMasked((current) => {
      const next = new Set(current);
      if (next.has(edgeId)) next.delete(edgeId);
      else next.add(edgeId);
      return next;
    });
    // Changing the selection returns the panel to the before-state.
    setResult(null);
    mutation.reset();
  };

  const clear = () => {
    setMasked(new Set());
    setResult(null);
    mutation.reset();
  };

  const run = () => {
    if (masked.size === 0) return;
    mutation.mutate({ masked_edges: Array.from(masked), mode });
  };

  const broken = useMemo<ReadonlySet<string>>(
    () => (result ? new Set(result.masked_edges) : new Set<string>()),
    [result],
  );

  const errorText = (() => {
    const error = mutation.error;
    if (!error) return null;
    if (isApiError(error)) {
      if (error.code === 'FORBIDDEN') return null; // gating bug on our side; logged by the client
      if (error.code === 'RATE_LIMITED') return 'Ablation is rate limited. It will be available again shortly.';
      return error.message;
    }
    return 'The ablation did not run.';
  })();
  const errorRequestId = isApiError(mutation.error) ? mutation.error.requestId : null;

  const buttonLabel = cooldown.active ? `Try again in ${cooldown.remaining}s` : 'Run ablation';

  if (insight.attention.length === 0) {
    return <p className="text-body-sm text-ink-200">No attention links were recorded for this insight.</p>;
  }

  return (
    <div className="flex flex-col gap-4">
      <p className="max-w-prose text-body-sm text-ink-200">
        Mark the links you think drive this result, then run the ablation to see whether the flag really
        depends on them.
      </p>

      <AttentionArcs
        tokens={insight.tokens}
        edges={insight.attention}
        masked={masked}
        broken={broken}
        interactive={canRun}
        onToggle={toggle}
      />

      <div className="flex flex-wrap items-center gap-3">
        <SegmentedControl
          ariaLabel="Masking mode"
          size="sm"
          options={MODE_OPTIONS}
          value={mode}
          onChange={(value) => {
            setMode(value);
            setResult(null);
            mutation.reset();
          }}
        />
        {canRun ? (
          <Button
            variant="primary"
            pending={mutation.isPending}
            pendingLabel="Running ablation…"
            disabled={masked.size === 0 || cooldown.active}
            onClick={run}
          >
            {buttonLabel}
          </Button>
        ) : (
          <Button variant="primary" disabled title="Ask an analyst to run this">
            Run ablation
          </Button>
        )}
        {masked.size > 0 ? (
          <Button variant="quiet" size="sm" onClick={clear}>
            Clear {masked.size} marked
          </Button>
        ) : (
          <span className="text-body-sm text-ink-200">
            {canRun ? 'Select at least one link.' : 'Read only. Your role cannot run ablations.'}
          </span>
        )}
      </div>

      {errorText ? (
        <div role="alert" className="text-body-sm text-ink-50">
          <p>{errorText}</p>
          {errorRequestId ? (
            <p className="text-ink-200">
              Request id <span className="select-all font-mono text-ink-50">{errorRequestId}</span>
            </p>
          ) : null}
          <p className="text-ink-200">The original result is unchanged.</p>
        </div>
      ) : null}

      <div className="grid grid-cols-2 gap-4">
        <div>
          <p className="text-body-sm text-ink-200">Confidence</p>
          <p className="nums text-h1 text-ink-50">{formatScore(confidence)}</p>
          <Meter value={confidence} label="Confidence after ablation" width={140} className="mt-1" />
          {result ? (
            <p className="mt-1 text-body-sm text-ink-200">was {formatScore(insight.trust.confidence)}</p>
          ) : null}
        </div>
        <div>
          <p className="text-body-sm text-ink-200">Vacuity</p>
          <p className="nums text-h1 text-ink-50">{formatScore(vacuity)}</p>
          <Meter value={vacuity} label="Vacuity after ablation" width={140} className="mt-1" />
          {result ? (
            <p className="mt-1 text-body-sm text-ink-200">was {formatScore(insight.trust.vacuity)}</p>
          ) : null}
        </div>
      </div>

      <div className="flex items-center gap-3">
        <span className="text-body-sm text-ink-200">Routing</span>
        <RoutingBadge
          bucket={shownRouting}
          stampKey={result?.delta.routing_changed ? result.run_id : undefined}
        />
      </div>

      <div aria-live="polite" className="flex flex-col gap-2">
        {result ? (
          <>
            <p className="text-body-sm text-ink-200">
              Confidence {formatSigned(result.delta.confidence)}, vacuity {formatSigned(result.delta.vacuity)}
              {result.delta.routing_changed ? ', routing changed' : ', routing unchanged'}.
            </p>
            <p className="max-w-prose text-body text-ink-50">{result.interpretation}</p>
            <p className="max-w-prose text-body font-semibold text-ink-50">
              {result.load_bearing ? VERDICT_TRUE : VERDICT_FALSE}
            </p>
          </>
        ) : null}
      </div>

      {history.length > 0 ? (
        <div>
          <h4 className="mb-2 text-body-sm font-semibold text-card-fg">Ablation history</h4>
          <ol className="flex flex-col divide-y divide-ink-500/30 rounded-panel border border-ink-500/40">
            {history.map((run) => (
              <li key={run.id} className="flex flex-wrap items-baseline gap-x-4 gap-y-1 px-3 py-2 text-body-sm">
                <span className="text-ink-200">{formatDateTime(run.created_at)}</span>
                <span className="font-mono text-micro text-ink-200">{run.masked_edges.join(', ')}</span>
                <span className="nums text-ink-50">
                  {formatScore(run.confidence_before)} to {formatScore(run.confidence_after)} (
                  {formatSigned(run.confidence_after - run.confidence_before)})
                </span>
                <span className="ml-auto text-ink-200">
                  {run.load_bearing ? 'load bearing' : 'not load bearing'}
                </span>
              </li>
            ))}
          </ol>
        </div>
      ) : null}
    </div>
  );
}
