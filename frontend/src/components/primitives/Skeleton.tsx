type SkeletonProps = {
  width?: string;
  height?: string;
  className?: string;
};

export function Skeleton({ width = "100%", height = "1rem", className = "" }: SkeletonProps) {
  return (
    <div
      aria-hidden="true"
      className={`bg-ink-500/20 rounded-input animate-pulse ${className}`}
      style={{ width, height }}
    />
  );
}