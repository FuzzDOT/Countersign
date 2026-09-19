<<<<<<< HEAD
import { markNemotronDown } from '@/lib/upstreamStatus';
import { ApiError, isApiError } from './errors';
import { getAccessToken, notifySessionEnded, setAccessToken } from './token';
import type { AuthResponse, ErrorEnvelope } from './types';

/**
 * The single fetch wrapper (brief §4.2).
 *
 *  - Bearer token from memory, `credentials: 'include'` so the HttpOnly
 *    refresh cookie travels.
 *  - 401 TOKEN_EXPIRED: POST /auth/refresh exactly once (single-flight, so N
 *    concurrent failures produce one refresh), then replay each failed request.
 *  - 401 REFRESH_REUSED: the token family is revoked. Wipe the token and tell
 *    the app to hard-redirect to /login. Never retried.
 *  - Every failure is an ApiError carrying request_id, so a toast can show it.
 */

export const API_BASE: string = (import.meta.env.VITE_API_BASE_URL ?? '/api/v1').replace(
  /\/+$/,
  '',
);

type QueryValue = string | number | boolean | null | undefined | readonly (string | number)[];
export type QueryParams = Record<string, QueryValue>;

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
  query?: QueryParams | undefined;
  json?: unknown;
  form?: FormData | undefined;
  headers?: Record<string, string> | undefined;
  signal?: AbortSignal | undefined;
  /** Abort and throw ApiError('TIMEOUT') after this many ms. */
  timeoutMs?: number | undefined;
  /** Send the bearer token. Defaults to true. */
  auth?: boolean | undefined;
  /** Treat `path` as a full URL or a root-relative URL, ignoring API_BASE. */
  raw?: boolean | undefined;
}

export function buildQuery(query: QueryParams | undefined): string {
  if (!query) return '';
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === '') continue;
    if (Array.isArray(value)) {
      for (const item of value as readonly (string | number)[]) params.append(key, String(item));
    } else {
      params.append(key, String(value as string | number | boolean));
    }
  }
  const text = params.toString();
  return text ? `?${text}` : '';
}

function buildUrl(path: string, opts: RequestOptions): string {
  const base = opts.raw ? path : `${API_BASE}${path}`;
  return `${base}${buildQuery(opts.query)}`;
}

async function execute(path: string, opts: RequestOptions): Promise<Response> {
  const headers: Record<string, string> = { Accept: 'application/json', ...opts.headers };
  const token = getAccessToken();
  if (opts.auth !== false && token) headers['Authorization'] = `Bearer ${token}`;

  let body: BodyInit | undefined;
  if (opts.form) {
    body = opts.form; // the browser sets the multipart boundary itself
  } else if (opts.json !== undefined) {
    headers['Content-Type'] = 'application/json';
    body = JSON.stringify(opts.json);
  }

  const controller = new AbortController();
  let timedOut = false;
  const onExternalAbort = () => controller.abort();
  if (opts.signal) {
    if (opts.signal.aborted) controller.abort();
    else opts.signal.addEventListener('abort', onExternalAbort, { once: true });
  }
  const timer =
    opts.timeoutMs !== undefined
      ? window.setTimeout(() => {
          timedOut = true;
          controller.abort();
        }, opts.timeoutMs)
      : undefined;

  try {
    const init: RequestInit = {
      method: opts.method ?? 'GET',
      headers,
      credentials: 'include',
      signal: controller.signal,
    };
    if (body !== undefined) init.body = body;
    return await fetch(buildUrl(path, opts), init);
  } catch (cause) {
    if (timedOut) {
      throw new ApiError({
        code: 'TIMEOUT',
        message: `The request took longer than ${Math.round((opts.timeoutMs ?? 0) / 1000)}s.`,
        status: 0,
        requestId: null,
      });
    }
    if (opts.signal?.aborted) throw cause; // caller cancelled; let it propagate untouched
    throw new ApiError({
      code: 'NETWORK_ERROR',
      message: 'Could not reach the server.',
      status: 0,
      requestId: null,
    });
  } finally {
    if (timer !== undefined) window.clearTimeout(timer);
    opts.signal?.removeEventListener('abort', onExternalAbort);
  }
}

async function toApiError(res: Response): Promise<ApiError> {
  const headerId = res.headers.get('X-Request-ID');
  try {
    const parsed: unknown = await res.json();
    if (
      typeof parsed === 'object' &&
      parsed !== null &&
      'error' in parsed &&
      typeof (parsed as ErrorEnvelope).error?.code === 'string'
    ) {
      return ApiError.fromBody((parsed as ErrorEnvelope).error, headerId);
    }
  } catch {
    // Not JSON (a proxy error page, say). Fall through to a generic error.
  }
  return new ApiError({
    code: `HTTP_${res.status}`,
    message: res.statusText || `Request failed with status ${res.status}.`,
    status: res.status,
    requestId: headerId,
  });
}

// ── refresh, single-flight ──────────────────────────────────────────────────

let refreshInFlight: Promise<string> | null = null;

/**
 * POST /auth/refresh. Concurrent callers share one request, which is the
 * "queue and replay" behaviour: every request that failed with TOKEN_EXPIRED
 * awaits the same promise and is then replayed with the new token.
 */
export function refreshSession(): Promise<string> {
  if (refreshInFlight) return refreshInFlight;
  refreshInFlight = (async () => {
    const res = await execute('/auth/refresh', { method: 'POST', auth: false });
    if (!res.ok) throw await toApiError(res);
    const data = (await res.json()) as AuthResponse;
    setAccessToken(data.access_token);
    return data.access_token;
  })().finally(() => {
    refreshInFlight = null;
  });
  return refreshInFlight;
}

/** Side effects that must happen for a code no matter who made the call. */
function observe(error: ApiError): void {
  if (error.code === 'REFRESH_REUSED') notifySessionEnded('reused');
  if (error.code === 'NEMOTRON_UNAVAILABLE') markNemotronDown();
  if (error.code === 'FORBIDDEN') {
    // The affordance should never have rendered. That is a gating bug on our
    // side (brief §4.3), so log it and show nothing.
    console.error('[permissions] FORBIDDEN', error.requestId, error.details);
  }
}

async function send(path: string, opts: RequestOptions): Promise<Response> {
  let res = await execute(path, opts);
  if (res.ok) return res;

  let error = await toApiError(res);
  const refreshable =
    opts.auth !== false &&
    !path.startsWith('/auth/') &&
    res.status === 401 &&
    (error.code === 'TOKEN_EXPIRED' || error.code === 'UNAUTHENTICATED');

  if (refreshable) {
    try {
      await refreshSession();
    } catch (refreshError) {
      const failure = isApiError(refreshError) ? refreshError : error;
      observe(failure);
      // Any failed refresh means there is no session left to resume.
      if (failure.code !== 'REFRESH_REUSED') notifySessionEnded('expired');
      throw failure;
    }
    res = await execute(path, opts);
    if (res.ok) return res;
    error = await toApiError(res);
  }

  observe(error);
  throw error;
}

/** JSON request. Resolves with the parsed body, or `undefined` for 204. */
export async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const res = await send(path, opts);
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

/** Binary request (audio). Same auth, refresh and error behaviour. */
export async function requestBinary(
  path: string,
  opts: RequestOptions = {},
): Promise<{ data: ArrayBuffer; contentType: string }> {
  const res = await send(path, { ...opts, headers: { Accept: '*/*', ...opts.headers } });
  return { data: await res.arrayBuffer(), contentType: res.headers.get('Content-Type') ?? '' };
}
=======
import { getAccessToken, setAccessToken, clearAuth } from "../lib/auth-context";

const BASE_URL = import.meta.env.VITE_API_BASE_URL;

type ApiErrorBody = {
  error: {
    code: string;
    message: string;
    details?: Record<string, unknown>;
  };
  request_id: string;
};

export class ApiError extends Error {
  code: string;
  details?: Record<string, unknown> | undefined; // was: details?: Record<string, unknown>
  requestId: string;
  status: number;
  
  constructor(body: ApiErrorBody, status: number) {
    super(body.error.message);
    this.code = body.error.code;
    this.details = body.error.details;
    this.requestId = body.request_id;
    this.status = status;
  }
}

let refreshPromise: Promise<void> | null = null;

async function refreshAccessToken(): Promise<void> {
  // Coalesce concurrent 401s into a single refresh call.
  if (!refreshPromise) {
    refreshPromise = fetch(`${BASE_URL}/auth/refresh`, {
      method: "POST",
      credentials: "include",
    })
      .then(async (res) => {
        if (!res.ok) {
          const body = (await res.json()) as ApiErrorBody;
          throw new ApiError(body, res.status);
        }
        const data = await res.json();
        setAccessToken(data.access_token);
      })
      .finally(() => {
        refreshPromise = null;
      });
  }
  return refreshPromise;
}

export async function apiFetch<T>(
  path: string,
  options: RequestInit = {},
  _retried = false
): Promise<T> {
  const token = getAccessToken();

  const res = await fetch(`${BASE_URL}${path}`, {
    ...options,
    credentials: "include", // refresh cookie must travel
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...options.headers,
    },
  });

  if (res.ok) {
    if (res.status === 204) return undefined as T;
    return res.json();
  }

  const body = (await res.json()) as ApiErrorBody;

  if (body.error.code === "TOKEN_EXPIRED" && !_retried) {
    await refreshAccessToken();
    return apiFetch<T>(path, options, true); // retry once after refresh
  }

  if (body.error.code === "REFRESH_REUSED") {
    clearAuth();
    window.location.href = "/login?notice=session_ended";
  }

  throw new ApiError(body, res.status);
}
>>>>>>> 04ef22a88f7d0a831b4ff1c0a31ca02ef46387d5
