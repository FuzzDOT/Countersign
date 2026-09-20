import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ThemeToggle } from '@/components/primitives/ThemeToggle';
import { useAuth } from '@/auth/useAuth';

/**
 * The account menu. `AuthProvider.signOut` existed and nothing called it, so
 * there was no way out of a session short of clearing cookies by hand — the
 * cs_refresh cookie is HttpOnly and pathed to /api/v1/auth, so a stale session
 * on the demo machine could not be ended from the browser at all.
 */
function AccountMenu() {
  const { me, signOut } = useAuth();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const wrapper = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (!wrapper.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('pointerdown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open]);

  return (
    <div ref={wrapper} className="relative">
      <button
        aria-label="Account menu"
        aria-expanded={open}
        aria-haspopup="menu"
        onClick={() => setOpen((was) => !was)}
        className="rounded-input border border-ctl-border bg-ctl-alt px-3 py-1.5 text-body-sm text-ctl-alt-fg
                   transition-colors duration-quick ease-out hover:border-verify hover:bg-ctl-alt-hover"
      >
        Account
      </button>

      {open ? (
        <div
          role="menu"
          className="absolute right-0 z-50 mt-2 w-60 rounded-panel border border-ink-500 bg-ink-700
                     p-1 text-body-sm text-ink-50 shadow-overlay"
        >
          {me ? (
            <div className="border-b border-ink-500 px-3 py-2">
              <p className="truncate text-ink-50">{me.email}</p>
              <p className="truncate text-body-sm text-ink-200">{me.org_name}</p>
            </div>
          ) : null}
          <button
            role="menuitem"
            onClick={() => {
              setOpen(false);
              navigate('/app/settings');
            }}
            className="w-full rounded-input px-3 py-2 text-left transition-colors duration-quick ease-out
                       hover:bg-ctl-alt-hover"
          >
            Settings
          </button>
          <button
            role="menuitem"
            onClick={() => {
              setOpen(false);
              // RequireAuth redirects to /login the moment status flips to
              // anonymous, so this does not navigate itself.
              void signOut();
            }}
            className="w-full rounded-input px-3 py-2 text-left transition-colors duration-quick ease-out
                       hover:bg-ctl-alt-hover"
          >
            Sign out
          </button>
        </div>
      ) : null}
    </div>
  );
}

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
    // z-20: <main> in AppShell is `relative` and paints later, so without a
    // stacking order of its own the account menu renders under the feed and
    // is unclickable.
    <div className="relative z-20 w-full">
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
          <AccountMenu />
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
