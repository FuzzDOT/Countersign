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
    <div
      className={cn('mx-auto flex max-w-prose flex-col items-start gap-3 px-6 py-12', className)}
    >
      <h2 className="text-h3 text-ink-50">{title}</h2>
      {children ? <div className="max-w-prose text-body text-ink-200">{children}</div> : null}
      {action}
    </div>
  );
}
