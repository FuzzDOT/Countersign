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
        'inline-flex size-9 items-center justify-center rounded-input border border-ctl-border bg-ctl-alt text-ctl-alt-fg',
        'transition-colors duration-quick ease-out hover:border-verify hover:bg-ctl-alt-hover',
        'disabled:cursor-not-allowed disabled:opacity-45',
        active && 'border-verify',
        className,
      )}
      {...rest}
    >
      {children}
    </button>
  );
});
