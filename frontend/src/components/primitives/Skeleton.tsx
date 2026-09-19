<<<<<<< HEAD
import { cn } from '@/lib/cn';

/**
 * Loading placeholder. It pulses opacity rather than shimmering, because the
 * brief allows exactly one gradient in the whole product and this is not it.
 * Callers give it the same dimensions as the loaded content so nothing shifts.
 */
export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden="true" className={cn('animate-pulse rounded-input bg-ink-500/40', className)} />;
}
=======
type SkeletonProps = {
  width?: string;
  height?: string;
  className?: string;
};

export function Skeleton({ width = "100%", height = "1rem", className = "" }: SkeletonProps) {
  return (
    <div
      aria-hidden="true"
      className={`bg-ink-500/20 rounded-input animate-pulse ${className}`}
      style={{ width, height }}
    />
  );
}
>>>>>>> 04ef22a88f7d0a831b4ff1c0a31ca02ef46387d5
