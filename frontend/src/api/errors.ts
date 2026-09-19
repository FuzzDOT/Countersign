import type { ErrorBody } from './types';

/**
 * Every failure from the API (and every failure to reach it) surfaces as an
 * ApiError. Branch on `code`, never on `message` (brief §4.3).
 *
 * Two codes are synthesised client-side and never sent by the server:
 *   NETWORK_ERROR  the request never got a response
 *   TIMEOUT        we gave up waiting (voice uses an 8s ceiling)
 */
export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly requestId: string | null;
  readonly details: Record<string, unknown>;

  constructor(init: {
    code: string;
    message: string;
    status: number;
    requestId: string | null;
    details?: Record<string, unknown>;
  }) {
    super(init.message);
    this.name = 'ApiError';
    this.code = init.code;
    this.status = init.status;
    this.requestId = init.requestId;
    this.details = init.details ?? {};
  }

  static fromBody(body: ErrorBody, headerRequestId: string | null): ApiError {
    return new ApiError({
      code: body.code,
      message: body.message,
      status: body.status,
      requestId: body.request_id || headerRequestId,
      details: body.details,
    });
  }
}

export function isApiError(value: unknown): value is ApiError {
  return value instanceof ApiError;
}

export function errorCode(value: unknown): string | null {
  return isApiError(value) ? value.code : null;
}

/** `details.retry_after_seconds` from RATE_LIMITED, clamped to something sane. */
export function retryAfterSeconds(error: unknown): number | null {
  if (!isApiError(error) || error.code !== 'RATE_LIMITED') return null;
  const raw = error.details['retry_after_seconds'];
  const seconds = typeof raw === 'number' ? raw : Number(raw);
  if (!Number.isFinite(seconds) || seconds <= 0) return 5;
  return Math.min(Math.ceil(seconds), 3600);
}

/** `details.fields` from VALIDATION_FAILED: field name to message. */
export function fieldErrors(error: unknown): Record<string, string> {
  if (!isApiError(error) || error.code !== 'VALIDATION_FAILED') return {};
  const fields = error.details['fields'];
  if (typeof fields !== 'object' || fields === null) return {};
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(fields)) {
    out[key] = typeof value === 'string' ? value : JSON.stringify(value);
  }
  return out;
}

/** Errors worth retrying automatically: transport failures and 5xx. */
export function isRetryable(error: unknown): boolean {
  if (!isApiError(error)) return false;
  if (error.code === 'NETWORK_ERROR' || error.code === 'TIMEOUT') return true;
  // 501 is a stage that has not landed; retrying will not change that.
  return error.status >= 500 && error.status !== 501;
}
