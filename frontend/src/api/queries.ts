import {
  keepPreviousData,
  useInfiniteQuery,
  useQuery,
  type QueryClient,
} from '@tanstack/react-query';
import type { GraphFilters } from '@/lib/graphFilters';
import { toGraphApiQuery } from '@/lib/graphFilters';
import type { FeedFilters } from '@/lib/feedFilters';
import { toApiQuery } from '@/lib/feedFilters';
import { preloadAudio } from '@/lib/audio';
import { api } from './endpoints';
import type { BriefingResponse } from './types';

/**
 * Query keys mirror the URL (brief §4.2): ['insights', filters],
 * ['insight', id], ['graph', graphFilters], ['evals', 'fragility'].
 *
 * Cache windows (brief §15): 30s for /insights and /graph, 5 minutes for
 * /evals/* because they only change when a script is re-run.
 */

const THIRTY_SECONDS = 30_000;
const FIVE_MINUTES = 5 * 60_000;

export const queryKeys = {
  insights: (filters: FeedFilters) => ['insights', filters] as const,
  insightStats: () => ['insights', 'stats'] as const,
  insight: (id: string) => ['insight', id] as const,
  graph: (filters: GraphFilters) => ['graph', filters] as const,
  entity: (id: string) => ['entity', id] as const,
  document: (id: string) => ['document', id] as const,
  fragility: () => ['evals', 'fragility'] as const,
  routingEval: () => ['evals', 'routing'] as const,
  calibration: () => ['evals', 'calibration'] as const,
  nemotronRuns: () => ['routing', 'runs'] as const,
  voiceFallback: () => ['voice', 'fallback'] as const,
};

export function useInsightsInfinite(filters: FeedFilters) {
  return useInfiniteQuery({
    queryKey: queryKeys.insights(filters),
    queryFn: ({ pageParam }) => api.insights.list(toApiQuery(filters), pageParam),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) =>
      last.pagination.has_more ? (last.pagination.next_cursor ?? undefined) : undefined,
    staleTime: THIRTY_SECONDS,
    // Keep the old list on screen while a filter change loads, so the feed
    // does not collapse to a skeleton on every keystroke.
    placeholderData: keepPreviousData,
  });
}

export function useInsightStats() {
  return useQuery({
    queryKey: queryKeys.insightStats(),
    queryFn: api.insights.stats,
    staleTime: THIRTY_SECONDS,
  });
}

export function useInsight(id: string | null) {
  return useQuery({
    queryKey: queryKeys.insight(id ?? 'none'),
    queryFn: () => api.insights.get(id as string),
    enabled: id !== null,
    staleTime: THIRTY_SECONDS,
  });
}

export function useGraph(filters: GraphFilters, enabled = true) {
  return useQuery({
    queryKey: queryKeys.graph(filters),
    queryFn: () => api.graph.get(toGraphApiQuery(filters)),
    staleTime: THIRTY_SECONDS,
    placeholderData: keepPreviousData,
    enabled,
  });
}

export function useEntity(id: string | null) {
  return useQuery({
    queryKey: queryKeys.entity(id ?? 'none'),
    queryFn: () => api.graph.entity(id as string),
    enabled: id !== null,
    staleTime: THIRTY_SECONDS,
  });
}

export function useDocument(id: string | null) {
  return useQuery({
    queryKey: queryKeys.document(id ?? 'none'),
    queryFn: () => api.documents.get(id as string),
    enabled: id !== null,
    staleTime: FIVE_MINUTES,
  });
}

export function useFragilityEval() {
  return useQuery({
    queryKey: queryKeys.fragility(),
    queryFn: api.evals.fragility,
    staleTime: FIVE_MINUTES,
  });
}

export function useRoutingEval() {
  return useQuery({
    queryKey: queryKeys.routingEval(),
    queryFn: api.evals.routing,
    staleTime: FIVE_MINUTES,
  });
}

export function useCalibrationEval() {
  return useQuery({
    queryKey: queryKeys.calibration(),
    queryFn: api.evals.calibration,
    staleTime: FIVE_MINUTES,
  });
}

export function useNemotronRuns() {
  return useInfiniteQuery({
    queryKey: queryKeys.nemotronRuns(),
    queryFn: ({ pageParam }) => api.routing.runs(pageParam),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) =>
      last.pagination.has_more ? (last.pagination.next_cursor ?? undefined) : undefined,
    staleTime: FIVE_MINUTES,
  });
}

/** Warm the recorded briefing so it is in cache before conference wifi fails (brief §12.3). */
export async function prefetchVoiceFallback(client: QueryClient): Promise<void> {
  await client.prefetchQuery({
    queryKey: queryKeys.voiceFallback(),
    queryFn: api.voice.fallback,
    staleTime: FIVE_MINUTES,
  });
  // The JSON is the easy part. Pull the audio bytes into memory too, so the
  // recorded briefing plays even if the network drops on stage.
  const briefing = client.getQueryData<BriefingResponse>(queryKeys.voiceFallback());
  if (briefing) preloadAudio(briefing.audio_url);
}

export function useVoiceFallback() {
  return useQuery({
    queryKey: queryKeys.voiceFallback(),
    queryFn: api.voice.fallback,
    staleTime: FIVE_MINUTES,
    retry: 1,
  });
}
