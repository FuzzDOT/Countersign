type RoutingSeverity = "escalate_now" | "flag_for_review" | "auto_file";

type BadgeProps = {
  routing?: RoutingSeverity; // routing severity — colour comes from here only
  neutral?: boolean; // for non-severity badges (counts, tags) — no colour signal
  children: React.ReactNode;
};

const routingClasses: Record<RoutingSeverity, string> = {
  escalate_now: "bg-stamp-red text-ink-50",
  flag_for_review: "bg-stamp-amber text-ink-50",
  auto_file: "bg-stamp-slate text-ink-50",
};

const routingLabels: Record<RoutingSeverity, string> = {
  escalate_now: "Escalated",
  flag_for_review: "Flagged for review",
  auto_file: "Auto-filed",
};

export function Badge({ routing, neutral, children }: BadgeProps) {
  const classes = routing ? routingClasses[routing] : "bg-ink-500/30 text-ink-200";

  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-micro font-medium ${classes}`}>
      {/* colour is never the sole carrier of meaning — always pair with a text label */}
      {routing && !neutral ? routingLabels[routing] : children}
    </span>
  );
}