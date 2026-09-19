import { useSyncExternalStore } from 'react';
import { getUpstreamState, subscribeUpstream } from '@/lib/upstreamStatus';

export function useUpstreamStatus() {
  return useSyncExternalStore(subscribeUpstream, getUpstreamState, getUpstreamState);
}
