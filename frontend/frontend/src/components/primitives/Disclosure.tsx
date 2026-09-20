import type { ReactNode } from 'react';

/** Native <details>: keyboard and screen-reader behaviour for free. Used for chart data tables. */
export function Disclosure({ summary, children }: { summary: string; children: ReactNode }) {
  return (
    <details className="group rounded-panel border border-ink-500/40 bg-ink-700/40">
      <summary className="cursor-pointer select-none px-3 py-2 text-body-sm font-medium text-ink-200 hover:text-ink-50">
        {summary}
      </summary>
      <div className="border-t border-ink-500/40 p-3">{children}</div>
    </details>
  );
}
