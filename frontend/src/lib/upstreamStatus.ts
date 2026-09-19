/**
 * Tiny external store for "is the Nemotron upstream down". Set by the API
 * layer when any request returns NEMOTRON_UNAVAILABLE; read by the feed to
 * show the single dismissible banner (brief §4.3: degrade, don't break).
 *
 * Deliberately not a state library. It is one boolean and a dismissed flag.
 */

interface UpstreamState {
  nemotronDown: boolean;
  dismissed: boolean;
}

let state: UpstreamState = { nemotronDown: false, dismissed: false };
const listeners = new Set<() => void>();

function emit(next: UpstreamState): void {
  state = next;
  listeners.forEach((listener) => listener());
}

export function markNemotronDown(): void {
  if (state.nemotronDown) return;
  emit({ nemotronDown: true, dismissed: false });
}

export function dismissNemotronBanner(): void {
  emit({ ...state, dismissed: true });
}

export function resetUpstreamStatus(): void {
  emit({ nemotronDown: false, dismissed: false });
}

export function subscribeUpstream(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function getUpstreamState(): UpstreamState {
  return state;
}
