import type { InsightOut } from '@/api/types';

export interface ResolverMarkerInfo {
  label: string;
  title: string;
}

/**
 * The marker text for who decided an insight, or null when nothing needs
 * saying. Kept out of the component so the row can decide whether to draw a
 * separator before it.
 */
export function resolverMarker(
  insight: Pick<InsightOut, 'resolved_by' | 'degraded' | 'routing'>,
  nemotronDown: boolean | undefined,
): ResolverMarkerInfo | null {
  if (insight.degraded) {
    return {
      label: 'classical fallback',
      title: 'Nemotron was unavailable, so the classical decision stands.',
    };
  }
  if (insight.resolved_by === 'nemotron') {
    return { label: 'nemotron', title: 'This routing decision was made by Nemotron.' };
  }
  if (nemotronDown && insight.routing === 'escalate_now') {
    return {
      label: 'reviewed classically',
      title: 'Nemotron is unavailable, so this was reviewed by the classical model only.',
    };
  }
  return null;
}
