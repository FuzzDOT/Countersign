import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { ThemeToggle } from '@/components/primitives/ThemeToggle';

function BackArrow() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" aria-hidden="true">
      <path d="M15 18l-6-6 6-6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/**
 * Header for the marketing sub-pages (/product, /security). Always carries a
 * real back affordance — a sub-page a judge lands on via deep link must have
 * a visible way home, not just the browser button.
 */
export function MarketingHeader({ title }: { title?: ReactNode }) {
  return (
    <header className="sticky top-0 z-30 border-b border-ink-500/40 bg-ink-900/85 backdrop-blur-xl">
      <div className="mx-auto flex h-16 max-w-6xl items-center justify-between gap-4 px-6">
        <Link
          to="/"
          className="inline-flex items-center gap-2 rounded-input border border-ctl-border
                     bg-ctl-alt px-3.5 py-2 font-mono text-micro uppercase tracking-[0.14em]
                     text-ctl-alt-fg transition-colors duration-quick ease-out
                     hover:border-verify hover:bg-ctl-alt-hover"
        >
          <BackArrow />
          Back
        </Link>

        {title ? (
          <span className="hidden font-mono text-micro uppercase tracking-[0.18em] text-ink-200 sm:block">
            {title}
          </span>
        ) : null}

        <ThemeToggle />
      </div>
    </header>
  );
}
