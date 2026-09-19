/**
 * Toasts are pushed from places that are not React (the QueryCache error
 * handler, the API layer) and rendered by <ToastViewport>. Every error toast
 * carries the request_id in mono, because during a demo that is how a problem
 * is diagnosed in ten seconds instead of two minutes (brief §4.2).
 */

export interface ToastMessage {
  id: number;
  title: string;
  description?: string | undefined;
  requestId?: string | null | undefined;
  tone: 'error' | 'info';
}

type Listener = (toast: ToastMessage) => void;
const listeners = new Set<Listener>();
let nextId = 1;

export function pushToast(toast: Omit<ToastMessage, 'id'>): void {
  const message: ToastMessage = { ...toast, id: nextId };
  nextId += 1;
  listeners.forEach((listener) => listener(message));
}

export function subscribeToasts(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
