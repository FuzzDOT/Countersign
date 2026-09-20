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