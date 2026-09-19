import { forwardRef } from "react";

type ButtonVariant = "primary" | "secondary" | "quiet" | "danger";

type ButtonProps = {
  variant?: ButtonVariant;
  pending?: boolean;
  pendingLabel?: string;
} & React.ButtonHTMLAttributes<HTMLButtonElement>;

const variantClasses: Record<ButtonVariant, string> = {
  primary: "bg-verify text-ink-900 hover:brightness-110",
  secondary: "bg-ink-700 text-ink-50 border border-ink-500/40 hover:bg-ink-500/20",
  quiet: "bg-transparent text-ink-200 hover:text-ink-50",
  danger: "bg-stamp-red text-ink-50 hover:brightness-110",
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ variant = "primary", pending = false, pendingLabel, disabled, children, className = "", ...props }, ref) => {
    return (
      <button
        ref={ref}
        disabled={disabled || pending}
        aria-busy={pending}
        className={`inline-flex items-center justify-center gap-2 rounded-input px-4 py-2 text-body font-medium
                    transition-colors duration-quick ease-out
                    disabled:opacity-50 disabled:cursor-not-allowed
                    ${variantClasses[variant]} ${className}`}
        {...props}
      >
        {pending ? pendingLabel ?? "Working…" : children}
      </button>
    );
  }
);
Button.displayName = "Button";