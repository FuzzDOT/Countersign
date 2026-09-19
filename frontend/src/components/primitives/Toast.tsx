import { useEffect, useState } from 'react';
import { subscribeToasts, type ToastMessage } from '@/lib/toastBus';
import { Button } from './Button';
import { CopyIcon } from './icons';

/**
 * Renders toasts pushed through lib/toastBus. Error toasts show the request id
 * in mono with a copy button; that is how a problem is traced during a demo.
 * Toasts are for failures only. Background success is noise (brief §6).
 */
export function ToastViewport() {
  const [toasts, setToasts] = useState<ToastMessage[]>([]);

  useEffect(
    () =>
      subscribeToasts((toast) => {
        setToasts((current) => [...current.slice(-3), toast]);
        if (toast.tone === 'info') {
          window.setTimeout(() => setToasts((c) => c.filter((t) => t.id !== toast.id)), 6000);
        }
      }),
    [],
  );

  const dismiss = (id: number) => setToasts((c) => c.filter((t) => t.id !== id));

  return (
    <div className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-[min(24rem,calc(100vw-2rem))] flex-col gap-2">
      {toasts.map((toast) => (
        <div
          key={toast.id}
          role={toast.tone === 'error' ? 'alert' : 'status'}
          className="pointer-events-auto rounded-panel border border-ink-500 bg-ink-700 p-3 shadow-overlay"
        >
          <p className="text-body font-semibold text-ink-50">{toast.title}</p>
          {toast.description ? <p className="mt-1 text-body-sm text-ink-200">{toast.description}</p> : null}
          {toast.requestId ? (
            <p className="mt-2 flex items-center gap-2 text-body-sm text-ink-200">
              <span>Request id</span>
              <span className="select-all font-mono text-ink-50">{toast.requestId}</span>
              <button
                type="button"
                aria-label="Copy request id"
                className="rounded-input p-1 hover:bg-ink-500/40"
                onClick={() => {
                  void navigator.clipboard?.writeText(toast.requestId ?? '');
                }}
              >
                <CopyIcon />
              </button>
            </p>
          ) : null}
          <div className="mt-2 flex justify-end">
            <Button size="sm" variant="quiet" onClick={() => dismiss(toast.id)}>
              Dismiss
            </Button>
          </div>
        </div>
      ))}
    </div>
  );
}
