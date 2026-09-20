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
        'inline-flex items-center rounded-input px-1.5 py-0.5 text-micro font-medium',
        tone === 'neutral' && 'bg-ink-500/50 text-ink-50',
        tone === 'outline' && 'border border-ink-200/60 text-ink-200',
        className,
      )}
      {...rest}
    />
  );
}
