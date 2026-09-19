import type { SVGProps } from 'react';

/**
 * A deliberately small icon set: 16px, 1.5px stroke, currentColor. Decorative
 * by default (aria-hidden); the IconButton that wraps one supplies the name.
 */
type IconProps = SVGProps<SVGSVGElement>;

function Base({ children, ...rest }: IconProps) {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...rest}
    >
      {children}
    </svg>
  );
}

export const PlayIcon = (p: IconProps) => (
  <Base {...p}>
    <path d="M4.5 3v10l8-5z" fill="currentColor" />
  </Base>
);
export const PauseIcon = (p: IconProps) => (
  <Base {...p}>
    <path d="M5 3v10M11 3v10" strokeWidth="2.2" />
  </Base>
);
export const CloseIcon = (p: IconProps) => (
  <Base {...p}>
    <path d="M3.5 3.5l9 9M12.5 3.5l-9 9" />
  </Base>
);
export const PlusIcon = (p: IconProps) => (
  <Base {...p}>
    <path d="M8 3v10M3 8h10" />
  </Base>
);
export const MinusIcon = (p: IconProps) => (
  <Base {...p}>
    <path d="M3 8h10" />
  </Base>
);
export const MicIcon = (p: IconProps) => (
  <Base {...p}>
    <rect x="6" y="2" width="4" height="7" rx="2" />
    <path d="M3.5 7.5a4.5 4.5 0 009 0M8 12v2" />
  </Base>
);
export const FitIcon = (p: IconProps) => (
  <Base {...p}>
    <path d="M2.5 6V2.5H6M10 2.5h3.5V6M13.5 10v3.5H10M6 13.5H2.5V10" />
  </Base>
);
export const TableIcon = (p: IconProps) => (
  <Base {...p}>
    <rect x="2" y="3" width="12" height="10" rx="1" />
    <path d="M2 7h12M6.5 3v10" />
  </Base>
);
export const GraphIcon = (p: IconProps) => (
  <Base {...p}>
    <circle cx="4" cy="4" r="1.6" />
    <circle cx="12" cy="5" r="1.6" />
    <circle cx="8" cy="12" r="1.6" />
    <path d="M5.4 4.4l5.2.4M4.8 5.4l2.4 5.2M11.2 6.4L8.8 10.6" />
  </Base>
);
export const FilterIcon = (p: IconProps) => (
  <Base {...p}>
    <path d="M2 3.5h12l-4.5 5.5v4l-3-1.5V9z" />
  </Base>
);
export const ChevronIcon = (p: IconProps) => (
  <Base {...p}>
    <path d="M5 6.5l3 3 3-3" />
  </Base>
);
export const CopyIcon = (p: IconProps) => (
  <Base {...p}>
    <rect x="5.5" y="5.5" width="8" height="8" rx="1" />
    <path d="M10.5 5.5V3.5a1 1 0 00-1-1h-6a1 1 0 00-1 1v6a1 1 0 001 1h2" />
  </Base>
);
export const SearchIcon = (p: IconProps) => (
  <Base {...p}>
    <circle cx="7" cy="7" r="4" />
    <path d="M10 10l3.5 3.5" />
  </Base>
);
