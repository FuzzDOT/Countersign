import { cn } from '@/lib/cn';

/**
 * Loading placeholder. It pulses opacity rather than shimmering, because the
 * brief allows exactly one gradient in the whole product and this is not it.
 * Callers give it the same dimensions as the loaded content so nothing shifts.
 */
export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden="true" className={cn('animate-pulse rounded-soft bg-ink-500/40', className)} />;
}
