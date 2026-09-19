<<<<<<< HEAD
import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from 'react';
import { Link, type LinkProps } from 'react-router-dom';
import { buttonClasses, type ButtonSize, type ButtonVariant } from '@/lib/buttonStyles';

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant | undefined;
  size?: ButtonSize | undefined;
  /** Disables the button and swaps the label. Never replaced by a spinner: that shifts layout. */
  pending?: boolean | undefined;
  pendingLabel?: ReactNode | undefined;
}

/** The button says what happens: "Open document", "Run ablation", "Recalibrate now". */
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    variant = 'secondary',
    size = 'md',
    pending = false,
    pendingLabel,
    className,
    children,
    disabled,
    type = 'button',
    ...rest
  },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      className={buttonClasses(variant, size, className)}
      disabled={disabled === true || pending}
      aria-busy={pending || undefined}
      {...rest}
    >
      {pending && pendingLabel !== undefined ? pendingLabel : children}
    </button>
  );
});

interface LinkButtonProps extends LinkProps {
  variant?: ButtonVariant | undefined;
  size?: ButtonSize | undefined;
}

export function LinkButton({ variant = 'secondary', size = 'md', className, ...rest }: LinkButtonProps) {
  return <Link className={buttonClasses(variant, size, className)} {...rest} />;
}
=======
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
>>>>>>> 04ef22a88f7d0a831b4ff1c0a31ca02ef46387d5
