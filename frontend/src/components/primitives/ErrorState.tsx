type ErrorStateProps = {
  message: string;
  requestId?: string;
  onRetry?: () => void;
};

export function ErrorState({ message, requestId, onRetry }: ErrorStateProps) {
  return (
    <div role="alert" className="flex flex-col items-center justify-center gap-2 py-16 text-center">
      <p className="text-h3 text-stamp-red">{message}</p>
      {requestId && <p className="text-body-sm text-ink-200 font-mono">request_id: {requestId}</p>}
      {onRetry && (
        <button onClick={onRetry} className="text-verify hover:underline text-body-sm">
          Try again
        </button>
      )}
    </div>
  );
}