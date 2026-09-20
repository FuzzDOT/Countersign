import { cloneElement, useId, useState, type ReactElement, type ReactNode } from 'react';

interface TooltipProps {
  content: ReactNode;
  children: ReactElement<{ 'aria-describedby'?: string }>;
}

/** Hover and focus tooltip for HTML elements. (SVG surfaces draw their own.) */
export function Tooltip({ content, children }: TooltipProps) {
  const id = useId();
  const [open, setOpen] = useState(false);
  return (
    <span
      className="relative inline-flex"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onFocus={() => setOpen(true)}
      onBlur={() => setOpen(false)}
    >
      {cloneElement(children, { 'aria-describedby': id })}
      <span
        id={id}
        role="tooltip"
        className={
          open
            ? 'pointer-events-none absolute bottom-full left-1/2 z-40 mb-1 w-max max-w-64 -translate-x-1/2 rounded-soft border border-ctl-border bg-ink-900 px-2 py-1 text-body-sm text-ink-50 shadow-overlay'
            : 'sr-only'
        }
      >
        {content}
      </span>
    </span>
  );
}
