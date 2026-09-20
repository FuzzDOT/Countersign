import { isApiError } from '@/api/errors';
import { cn } from '@/lib/cn';
import { Button } from './Button';

/** A human sentence for an error. Branches on `code`, never on the server's message. */
function describeError(error: unknown): string {
  if (!isApiError(error)) return 'Something went wrong on our side.';
  switch (error.code) {
    case 'NETWORK_ERROR':
      return 'Could not reach the server. Check your connection and try again.';
    case 'TIMEOUT':
      return 'The server took too long to answer.';
    case 'FORBIDDEN':
      return 'You do not have access to this.';
    case 'INSIGHT_NOT_FOUND':
      return 'That insight does not exist in this organization.';
    case 'DOCUMENT_NOT_FOUND':
      return 'That document does not exist in this organization.';
    case 'NOT_FOUND':
      return 'That does not exist in this organization.';
    case 'RATE_LIMITED':
      return 'Too many requests. Wait a moment and try again.';
    case 'NEMOTRON_UNAVAILABLE':
      return 'The language model upstream is unavailable. Classical results are unaffected.';
    default:
      return error.status === 501
        ? 'This endpoint is not live yet. Run the API with MOCK_MODE=1 to develop against it.'
        : error.message || 'Something went wrong on our side.';
  }
}

interface ErrorStateProps {
  error: unknown;
  onRetry?: (() => void) | undefined;
  title?: string | undefined;
  className?: string | undefined;
  compact?: boolean | undefined;
}

/** Designed error state. Always shows the request id in mono so a failure can be traced in seconds. */
export function ErrorState({ error, onRetry, title = 'That did not load', className, compact }: ErrorStateProps) {
  const requestId = isApiError(error) ? error.requestId : null;
  return (
    <div
      role="alert"
      className={cn('flex flex-col items-start gap-3', compact ? 'p-4' : 'mx-auto max-w-prose px-6 py-12', className)}
    >
      <h2 className={compact ? 'text-body font-semibold text-ink-50' : 'text-h3 text-ink-50'}>{title}</h2>
      <p className="text-body text-ink-200">{describeError(error)}</p>
      {requestId ? (
        <p className="text-body-sm text-ink-200">
          Request id <span className="select-all font-mono text-ink-50">{requestId}</span>
        </p>
      ) : null}
      {onRetry ? (
        <Button variant="secondary" onClick={onRetry}>
          Try again
        </Button>
      ) : null}
    </div>
  );
}
