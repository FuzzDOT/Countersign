import { cn } from './cn';

export type ButtonVariant = 'primary' | 'secondary' | 'quiet' | 'danger';
export type ButtonSize = 'sm' | 'md' | 'lg';

/**
 * Shared by <Button> and <LinkButton> so a link that looks like a button is
 * styled by the same source.
 *
 * Contrast note: every variant here uses a SOLID background or a full-opacity
 * border. Translucent fills (`bg-ink-700/60`) disappear against the navy
 * ground — buttons are the one place opacity is not worth the elegance.
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
    'group relative inline-flex select-none items-center justify-center gap-2',
    'rounded-input font-semibold tracking-tight whitespace-nowrap',
    'transition-all duration-quick ease-out active:translate-y-px',
    'disabled:cursor-not-allowed disabled:opacity-45 aria-disabled:cursor-not-allowed aria-disabled:opacity-45',
    size === 'sm' && 'h-9 px-4 text-body-sm',
    size === 'md' && 'h-11 px-6 text-body-sm',
    size === 'lg' && 'h-12 px-8 text-body',

    // Primary: solid paper-white on ink. Highest contrast thing on the page.
    // On hover it inverts to the accent — a clear, unmistakable state change
    // rather than a 5% brightness nudge nobody can perceive.
    variant === 'primary' && [
      'bg-ink-50 text-ink-900',
      'shadow-[0_10px_30px_-10px_rgba(0,0,0,0.55)]',
      'hover:-translate-y-0.5 hover:bg-verify hover:text-ink-50',
      'hover:shadow-[0_14px_36px_-10px_color-mix(in_srgb,var(--verify)_55%,transparent)]',
    ],

    // Secondary: SOLID raised surface, full-strength 1px border, bright label.
    // This is the variant that was previously invisible.
    variant === 'secondary' && [
      'bg-ink-700 text-ink-50 border border-ink-200/45',
      'shadow-[0_2px_8px_-4px_rgba(0,0,0,0.4)]',
      // Hover fills to a solid, clearly darker/denser surface with a full
      // accent border, so the state change is obvious at a glance.
      'hover:-translate-y-0.5 hover:border-verify hover:bg-ink-500',
      'hover:shadow-[0_10px_28px_-10px_rgba(0,0,0,0.6)]',
    ],

    // Quiet: no fill at rest, but a real readable label and a solid hover fill.
    variant === 'quiet' && 'text-ink-50/85 hover:bg-ink-500/60 hover:text-ink-50',

    // Danger: heavy outline, never --stamp-red.
    variant === 'danger' && 'border-2 border-ink-050/70 text-ink-50 hover:bg-ink-500/60',

    extra,
  );
}
