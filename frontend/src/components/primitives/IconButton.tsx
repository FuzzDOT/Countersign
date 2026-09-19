import { forwardRef } from "react";

type IconButtonProps = {
  icon: React.ReactNode;
  "aria-label": string; // required, not optional — icon-only buttons must be labeled
} & Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, "children">;

export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(
  ({ icon, className = "", ...props }, ref) => {
    return (
      <button
        ref={ref}
        className={`inline-flex items-center justify-center rounded-input p-2
                    text-ink-200 hover:text-verify transition-colors duration-quick ease-out
                    disabled:opacity-50 disabled:cursor-not-allowed ${className}`}
        {...props}
      >
        {icon}
      </button>
    );
  }
);
IconButton.displayName = "IconButton";