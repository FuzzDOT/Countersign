import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from 'react';
import { cn } from '@/lib/cn';

interface IconButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'aria-label'> {
  /** Required: an icon has no visible text, so it needs an accessible name. */
  label: string;
  children: ReactNode;
  active?: boolean | undefined;
}

export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { label, children, className, active, type = 'button', ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      aria-label={label}
      title={label}
      aria-pressed={active}
      className={cn(
        'inline-flex size-9 items-center justify-center rounded-input text-ink-200',
        'transition-colors duration-instant ease-out hover:bg-ink-700 hover:text-ink-50',
        'disabled:cursor-not-allowed disabled:opacity-50',
        active && 'bg-ink-500/50 text-ink-50',
        className,
      )}
      {...rest}
    >
      {children}
    </button>
  );
});
