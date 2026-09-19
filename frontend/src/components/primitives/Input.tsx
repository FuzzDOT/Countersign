import { forwardRef, useId } from "react";

type InputProps = {
  label: string;
  error?: string | undefined;
  hint?: string | undefined;
} & React.InputHTMLAttributes<HTMLInputElement>;

export const Input = forwardRef<HTMLInputElement, InputProps>(
  ({ label, error, hint, id, className = "", ...props }, ref) => {
    const generatedId = useId();
    const inputId = id ?? generatedId;
    const errorId = `${inputId}-error`;
    const hintId = `${inputId}-hint`;

    return (
      <div className="flex flex-col gap-1">
        <label htmlFor={inputId} className="text-body-sm text-ink-200">
          {label}
        </label>
        <input
          ref={ref}
          id={inputId}
          aria-invalid={!!error}
          aria-describedby={error ? errorId : hint ? hintId : undefined}
          className={`rounded-input px-3 py-2 bg-ink-900 text-ink-50 border
                      ${error ? "border-stamp-red" : "border-ink-500/40"}
                      transition-colors duration-quick ease-out ${className}`}
          {...props}
        />
        {error && (
          <span id={errorId} role="alert" className="text-body-sm text-stamp-red">
            {error}
          </span>
        )}
        {!error && hint && (
          <span id={hintId} className="text-body-sm text-ink-200">
            {hint}
          </span>
        )}
      </div>
    );
  }
);
Input.displayName = "Input";