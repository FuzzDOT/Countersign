type EmptyStateProps = {
  title: string;
  description?: string;
  action?: React.ReactNode;
};

export function EmptyState({ title, description, action }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-16 text-center">
      <p className="text-h3 text-ink-50">{title}</p>
      {description && <p className="text-body-sm text-ink-200 max-w-prose">{description}</p>}
      {action}
    </div>
  );
}