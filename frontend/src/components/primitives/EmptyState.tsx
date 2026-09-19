<<<<<<< HEAD
import type { ReactNode } from 'react';
import { cn } from '@/lib/cn';

interface EmptyStateProps {
  title: string;
  children?: ReactNode;
  action?: ReactNode;
  className?: string;
}

export function EmptyState({ title, children, action, className }: EmptyStateProps) {
  return (
    <div className={cn('mx-auto flex max-w-prose flex-col items-start gap-3 px-6 py-12', className)}>
      <h2 className="text-h3 text-ink-50">{title}</h2>
      {children ? <div className="max-w-prose text-body text-ink-200">{children}</div> : null}
      {action}
    </div>
  );
}
=======
type EmptyStateProps = {
  title: string;
  description?: string;
  action?: React.ReactNode;
};

export function EmptyState({ title, description, action }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-16 text-center">
      <p className="text-h3 text-ink-50">{title}</p>
      {description && <p className="text-body-sm text-ink-200 max-w-prose">{description}</p>}
      {action}
    </div>
  );
}
>>>>>>> 04ef22a88f7d0a831b4ff1c0a31ca02ef46387d5
