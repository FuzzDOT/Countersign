type PaperSurfaceProps = {
  children: React.ReactNode;
  className?: string;
};

export function PaperSurface({ children, className = '' }: PaperSurfaceProps) {
  return <div className={`paper-surface p-6 ${className}`}>{children}</div>;
}
