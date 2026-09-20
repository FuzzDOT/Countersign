import { useState } from 'react';
import { ThemeToggle } from '@/components/primitives/ThemeToggle';

export function TopBar({
  orgName,
  scenarioName,
  jobProgress,
  onSearch,
}: {
  orgName: string;
  scenarioName: string;
  jobProgress: number | null;
  onSearch: (q: string) => void;
}) {
  const [query, setQuery] = useState('');

  return (
    <div className="relative w-full">
      <div className="flex items-center justify-between gap-6 border-b border-ink-500/40 bg-ink-700/70 px-6 py-3 text-ink-50 backdrop-blur-xl">
        <div className="flex min-w-0 items-center gap-3">
          <span className="truncate font-semibold tracking-tight">{orgName}</span>
          <span className="text-ink-200/60">/</span>
          <span className="truncate font-mono text-body-sm text-ink-200">{scenarioName}</span>
        </div>

        <input
          type="search"
          placeholder="Search insights…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && onSearch(query)}
          className="h-9 w-64 rounded-input border border-fld-border bg-fld px-4 text-body-sm text-ctl-alt-fg
                     placeholder:text-fld-ph focus-visible:border-verify"
        />

        <div className="flex items-center gap-3">
          <ThemeToggle />
          <button
            aria-label="Account menu"
            className="rounded-input border border-ctl-border bg-ctl-alt px-3 py-1.5 text-body-sm text-ctl-alt-fg
                       transition-colors duration-quick ease-out hover:border-verify hover:bg-ctl-alt-hover"
          >
            Account
          </button>
        </div>
      </div>

      {jobProgress != null && (
        <div className="absolute bottom-0 left-0 h-[2px] w-full bg-ink-900">
          <div
            className="h-full bg-verify transition-[width] duration-quick ease-out"
            style={{ width: `${jobProgress}%` }}
          />
        </div>
      )}
    </div>
  );
}
