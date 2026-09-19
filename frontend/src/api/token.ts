/**
 * The access token lives here and nowhere else: a module-level variable.
 * Never localStorage, never sessionStorage (banned by lint and by the brief).
 * The refresh token is an HttpOnly cookie this code cannot see and must not
 * try to read.
 */

let accessToken: string | null = null;

export function getAccessToken(): string | null {
  return accessToken;
}

export function setAccessToken(token: string | null): void {
  accessToken = token;
}

export type SessionEndReason = 'expired' | 'reused';
type SessionListener = (reason: SessionEndReason) => void;

const listeners = new Set<SessionListener>();

/** Subscribe to "the session is over, go to /login". Returns an unsubscribe. */
export function onSessionEnded(listener: SessionListener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function notifySessionEnded(reason: SessionEndReason): void {
  accessToken = null;
  listeners.forEach((listener) => listener(reason));
}
