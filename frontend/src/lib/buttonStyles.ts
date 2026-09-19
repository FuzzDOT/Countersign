import { cn } from './cn';

export type ButtonVariant = 'primary' | 'secondary' | 'quiet' | 'danger';
export type ButtonSize = 'sm' | 'md';

/**
 * Shared by <Button> and <LinkButton> so a link that looks like a button is
 * styled by the same source.
 *
 * `danger` is deliberately not red. --stamp-red means "this insight is
 * escalated" and nothing else (brief §2.2), so a destructive action is marked
 * by a heavier outline instead.
 */
export function buttonClasses(
  variant: ButtonVariant = 'secondary',
  size: ButtonSize = 'md',
  extra?: string,
): string {
  return cn(
    'inline-flex select-none items-center justify-center gap-2 rounded-input font-medium',
    'transition-colors duration-instant ease-out',
    'disabled:cursor-not-allowed disabled:opacity-50 aria-disabled:cursor-not-allowed aria-disabled:opacity-50',
    size === 'sm' ? 'h-8 px-3 text-body-sm' : 'h-10 px-4 text-body-sm',
    variant === 'primary' && 'bg-verify text-ink-900 hover:brightness-110',
    variant === 'secondary' &&
      'border border-ink-500 bg-ink-700 text-ink-50 hover:bg-ink-500/40 disabled:hover:bg-ink-700',
    variant === 'quiet' && 'text-ink-200 hover:bg-ink-700 hover:text-ink-50',
    variant === 'danger' && 'border-2 border-ink-200 text-ink-50 hover:bg-ink-500/40',
    extra,
  );
}
