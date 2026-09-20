import type { InsightOut } from '@/api/types';
import { Badge } from '@/components/primitives/Badge';
import { resolverMarker } from '@/lib/resolver';

interface ResolverMarkerProps {
  insight: Pick<InsightOut, 'resolved_by' | 'degraded' | 'routing'>;
  /** True when a NEMOTRON_UNAVAILABLE has been seen this session. */
  nemotronDown?: boolean | undefined;
}

/**
 * Who decided. Nemotron gets a small outlined marker; a degraded insight says
 * so plainly, because a classical fallback must not look like a normal result
 * (brief §7.3, §4.3).
 */
export function ResolverMarker({ insight, nemotronDown }: ResolverMarkerProps) {
  const info = resolverMarker(insight, nemotronDown);
  if (!info) return null;
  return (
    <Badge tone="outline" title={info.title}>
      {info.label}
    </Badge>
  );
}
