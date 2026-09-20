import { forwardRef, useId, type InputHTMLAttributes, type ReactNode } from 'react';
import { cn } from '@/lib/cn';

interface InputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'id'> {
  label: string;
  hint?: ReactNode | undefined;
  /** Field-level error from `details.fields`. */
  error?: string | undefined;
  /** Hide the visible label (still announced) for compact search boxes. */
  hideLabel?: boolean | undefined;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { label, hint, error, hideLabel, className, ...rest },
  ref,
) {
  const id = useId();
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className={cn('text-body-sm font-medium text-ink-50', hideLabel && 'sr-only')}>
        {label}
      </label>
      <input
        ref={ref}
        id={id}
        aria-invalid={error ? true : undefined}
        aria-describedby={cn(hint && hintId, error && errorId) || undefined}
        className={cn(
          'h-10 rounded-input border border-ink-500 bg-ink-900 px-3 text-body text-ink-50',
          'placeholder:text-ink-200/70 focus-visible:border-verify',
          error && 'border-ink-200',
          className,
        )}
        {...rest}
      />
      {hint ? (
        <p id={hintId} className="text-body-sm text-ink-200">
          {hint}
        </p>
      ) : null}
      {error ? (
        <p id={errorId} role="alert" className="text-body-sm text-ink-50">
          {error}
        </p>
      ) : null}
    </div>
  );
});
