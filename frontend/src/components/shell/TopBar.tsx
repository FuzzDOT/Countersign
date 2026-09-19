import { useState } from "react";

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
  const [query, setQuery] = useState("");

  return (
    <div className="relative w-full">
      <div className="flex items-center justify-between px-6 py-3 bg-ink-700 text-ink-50">
        <div className="flex items-center gap-3">
          <span className="font-semibold">{orgName}</span>
          <span className="text-ink-200">/</span>
          <span className="text-ink-200">{scenarioName}</span>
        </div>

        <input
          type="search"
          placeholder="Search insights…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && onSearch(query)}
          className="rounded-input px-3 py-1 bg-ink-900 text-ink-50 w-64"
        />

        <button aria-label="Account menu" className="text-ink-200 hover:text-verify">
          Account
        </button>
      </div>

      {jobProgress != null && (
        <div className="absolute left-0 bottom-0 w-full h-[2px] bg-ink-900">
          <div
            className="h-full bg-verify transition-[width] duration-quick ease-out"
            style={{ width: `${jobProgress}%` }}
          />
        </div>
      )}
    </div>
  );
}