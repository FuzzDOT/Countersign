import { cn } from './cn';

export type ButtonVariant = 'primary' | 'secondary' | 'quiet' | 'danger';
export type ButtonSize = 'sm' | 'md' | 'lg';

/**
 * Shared by <Button> and <LinkButton> so a link that looks like a button is
 * styled by the same source.
 *
 * Contrast rule: every variant uses a SOLID fill and a full-opacity border.
 * Translucent button surfaces disappear against the deep navy ground — a
 * control is the one place where opacity is not worth the elegance.
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

    // Primary: solid near-white pill, dark label. The brightest thing on the
    // page. Hover inverts to the accent so the state change is unmistakable.
    variant === 'primary' && [
      'bg-ctl text-ctl-fg',
      'shadow-[0_10px_30px_-10px_rgba(0,0,0,0.55)]',
      'hover:-translate-y-0.5 hover:bg-verify hover:text-ctl-fg',
    ],

    // Secondary: solid raised slate surface with a bright 1px border and a
    // near-white label — readable at a glance against the navy hero.
    variant === 'secondary' && [
      'border bg-ctl-alt text-ctl-alt-fg border-ctl-border',
      'shadow-[0_4px_14px_-6px_rgba(0,0,0,0.5)]',
      'hover:-translate-y-0.5 hover:bg-ctl-alt-hover hover:border-verify',
    ],

    variant === 'quiet' && 'text-ctl-alt-fg/85 hover:bg-ctl-alt-hover hover:text-ctl-alt-fg',

    variant === 'danger' &&
      'border-2 border-ctl-border text-ctl-alt-fg hover:bg-ctl-alt-hover',

    extra,
  );
}
