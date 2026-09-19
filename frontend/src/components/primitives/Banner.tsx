import type { ReactNode } from 'react';
import { Button } from './Button';

interface BannerProps {
  children: ReactNode;
  onDismiss?: () => void;
}

/** A single quiet, dismissible notice. Neutral ink: severity colours are reserved for routing. */
export function Banner({ children, onDismiss }: BannerProps) {
  return (
    <div
      role="status"
      className="flex items-start gap-3 rounded-panel border border-ink-500/60 bg-ink-700 px-4 py-3 text-body-sm text-ink-50"
    >
      <div className="flex-1">{children}</div>
      {onDismiss ? (
        <Button variant="quiet" size="sm" onClick={onDismiss}>
          Dismiss
        </Button>
      ) : null}
    </div>
  );
}
