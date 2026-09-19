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