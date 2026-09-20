import type { ReactNode } from 'react';
import { NavLink, Link } from 'react-router-dom';
import { useRailState } from '@/hooks/useRailState';
import { cn } from '@/lib/cn';

type RailItem = {
  label: string;
  path: string;
  icon: ReactNode;
  badge?: number | undefined;
};

/* Icons kept inline and minimal — one stroke weight, one visual language. */
const ICON = 'h-[18px] w-[18px]';

function FeedIcon() {
  return (
    <svg className={ICON} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
      <path d="M4 6h16M4 12h16M4 18h10" strokeLinecap="round" />
    </svg>
  );
}
function GraphIcon() {
  return (
    <svg className={ICON} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
      <circle cx="12" cy="5" r="2.5" />
      <circle cx="5.5" cy="18" r="2.5" />
      <circle cx="18.5" cy="18" r="2.5" />
      <path d="M10.5 7.2L7 15.6M13.5 7.2L17 15.6M8 18h8" strokeLinecap="round" />
    </svg>
  );
}
function EvidenceIcon() {
  return (
    <svg className={ICON} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
      <path d="M4 19V5M4 19h16" strokeLinecap="round" />
      <path d="M8 16V11M12.5 16V7.5M17 16v-3" strokeLinecap="round" />
    </svg>
  );
}
function VoiceIcon() {
  return (
    <svg className={ICON} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
      <rect x="9" y="3" width="6" height="11" rx="3" />
      <path d="M5.5 11.5a6.5 6.5 0 0 0 13 0M12 18v3" strokeLinecap="round" />
    </svg>
  );
}
function IngestIcon() {
  return (
    <svg className={ICON} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
      <path d="M12 16V4m0 0L8 8m4-4l4 4" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2" strokeLinecap="round" />
    </svg>
  );
}
function SettingsIcon() {
  return (
    <svg className={ICON} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
      <circle cx="12" cy="12" r="3" />
      <path d="M12 3v2.5M12 18.5V21M3 12h2.5M18.5 12H21M5.6 5.6l1.8 1.8M16.6 16.6l1.8 1.8M5.6 18.4l1.8-1.8M16.6 7.4l1.8-1.8" strokeLinecap="round" />
    </svg>
  );
}

function LogoMark() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <rect x="3" y="3" width="18" height="18" rx="3" stroke="currentColor" strokeWidth="1.6" />
      <path d="M8 12.5l2.5 2.5L16 9.5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  );
}

function ChevronIcon({ expanded }: { expanded: boolean }) {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.2"
      aria-hidden="true"
      className={cn('transition-transform duration-quick ease-out', !expanded && 'rotate-180')}
    >
      <path d="M15 18l-6-6 6-6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function RailNav({ escalateCount }: { escalateCount?: number | undefined }) {
  const { expanded, toggle } = useRailState();

  const items: RailItem[] = [
    { label: 'Feed', path: '/app/feed', icon: <FeedIcon />, badge: escalateCount },
    { label: 'Graph', path: '/app/graph', icon: <GraphIcon /> },
    { label: 'Evidence', path: '/app/evidence', icon: <EvidenceIcon /> },
    { label: 'Voice', path: '/app/voice', icon: <VoiceIcon /> },
    { label: 'Ingest', path: '/app/ingest', icon: <IngestIcon /> },
    { label: 'Settings', path: '/app/settings', icon: <SettingsIcon /> },
  ];

  return (
    <nav
      aria-label="Main"
      className={cn(
        'flex h-full shrink-0 flex-col border-r border-ink-500/40 bg-ink-900 text-ink-50',
        'transition-[width] duration-quick ease-out',
        expanded ? 'w-[var(--rail-expanded)]' : 'w-[var(--rail-collapsed)]',
      )}
    >
      {/* Wordmark — same mark and mono-caps treatment as the landing nav. */}
      <div
        className={cn(
          'flex h-16 items-center border-b border-ink-500/40',
          expanded ? 'justify-between px-4' : 'justify-center px-0',
        )}
      >
        <Link
          to="/"
          aria-label="Countersign home"
          className="flex items-center gap-2.5 text-ink-50 transition-opacity duration-quick ease-out hover:opacity-80"
        >
          <LogoMark />
          {expanded && (
            <span className="font-mono text-body-sm font-semibold uppercase tracking-[0.18em]">
              Countersign
            </span>
          )}
        </Link>

        {expanded && (
          <button
            onClick={toggle}
            aria-label="Collapse navigation"
            aria-expanded={expanded}
            className="rounded-input border border-ctl-border bg-ctl-alt p-1.5 text-ctl-alt-fg transition-colors duration-quick ease-out hover:border-verify hover:bg-ctl-alt-hover"
          >
            <ChevronIcon expanded={expanded} />
          </button>
        )}
      </div>

      {!expanded && (
        <button
          onClick={toggle}
          aria-label="Expand navigation"
          aria-expanded={expanded}
          className="mx-auto mt-3 rounded-input border border-ctl-border bg-ctl-alt p-1.5 text-ctl-alt-fg transition-colors duration-quick ease-out hover:border-verify hover:bg-ctl-alt-hover"
        >
          <ChevronIcon expanded={expanded} />
        </button>
      )}

      <ul className="mt-3 flex flex-col gap-0.5">
        {items.map((item) => (
          <li key={item.path}>
            <NavLink
              to={item.path}
              /* Collapsed: the icon carries the meaning and the accessible
                 name comes from title + sr-only text. No truncated single
                 letter — "F"/"G"/"E" is noise, not navigation. */
              title={!expanded ? item.label : undefined}
              className={({ isActive }) =>
                cn(
                  'relative mx-2 my-0.5 flex items-center rounded-input py-2.5',
                  'transition-colors duration-quick ease-out',
                  expanded ? 'gap-3 px-3' : 'justify-center px-0',
                  isActive
                    ? 'bg-verify/20 text-ink-050'
                    : 'text-ink-50/70 hover:bg-ink-500/50 hover:text-ink-50',
                )
              }
            >
              {({ isActive }) => (
                <>
                  {isActive && (
                    <span className="absolute left-0 top-1/2 h-4 w-[3px] -translate-y-1/2 rounded-full bg-verify" />
                  )}
                  <span className="shrink-0">{item.icon}</span>

                  {expanded ? (
                    <span className="truncate">{item.label}</span>
                  ) : (
                    <span className="sr-only">{item.label}</span>
                  )}

                  {item.badge != null &&
                    item.badge > 0 &&
                    (expanded ? (
                      <span className="ml-auto rounded-full bg-stamp-red px-2 text-micro font-medium text-ink-50">
                        {item.badge}
                      </span>
                    ) : (
                      /* Collapsed: a dot, not a number — a count in 72px
                         either overflows or shrinks below legibility. */
                      <span
                        className="absolute right-2.5 top-2 h-2 w-2 rounded-full bg-stamp-red ring-2 ring-ink-900"
                        aria-hidden="true"
                      />
                    ))}
                </>
              )}
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  );
}
