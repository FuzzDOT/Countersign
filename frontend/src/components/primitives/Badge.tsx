import type { HTMLAttributes } from 'react';
import { cn } from '@/lib/cn';

interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  tone?: 'neutral' | 'outline' | undefined;
}

/**
 * Small status text. Sentence case, weight and colour doing the hierarchy, no
 * all-caps (brief §2.3). Not for routing severity: use <RoutingBadge>.
 */
export function Badge({ tone = 'neutral', className, ...rest }: BadgeProps) {
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-input px-2 py-0.5 text-micro font-medium',
        tone === 'neutral' && 'border border-card-border bg-card text-card-fg',
        tone === 'outline' && 'border border-card-border text-card-muted',
        className,
      )}
      {...rest}
    />
  );
}
