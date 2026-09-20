import { useEffect, useRef, useState } from 'react';
import { API_BASE, refreshSession } from '@/api/client';
import { api } from '@/api/endpoints';
import { getAccessToken } from '@/api/token';
import type { JobOut } from '@/api/types';

/**
 * Live job progress (contract notes, "Job progress").
 *
 * `ws://…/ws/jobs/{id}?token=<access_jwt>` pushes a `JobOut` on every state
 * transition and closes 1000 when the job is done. The token is a query
 * parameter because a browser cannot set headers on a WebSocket handshake;
 * a bad one closes 1008 before the socket ever opens.
 *
 * Falls back to polling `GET /ingest/jobs/{id}` every 2s if the socket has
 * not opened within 3s, which is the behaviour the contract notes specify.
 * That endpoint is a single indexed row read and is safe to poll.
 */

const OPEN_GRACE_MS = 3_000;
const POLL_MS = 2_000;
const WS_POLICY_VIOLATION = 1008;

function socketUrl(jobId: string, token: string): string {
  // API_BASE is either absolute (http://localhost:8000/api/v1) or root-relative
  // (/api/v1, proxied by the dev server). Resolve against the page so both
  // produce a valid ws:// or wss:// origin.
  const http = new URL(API_BASE, window.location.origin);
  http.protocol = http.protocol === 'https:' ? 'wss:' : 'ws:';
  http.pathname = `${http.pathname.replace(/\/+$/, '')}/ws/jobs/${encodeURIComponent(jobId)}`;
  http.searchParams.set('token', token);
  return http.toString();
}

export function isTerminal(job: JobOut | null): boolean {
  return job?.state === 'done' || job?.state === 'failed';
}

/** Overall completion 0..1, for the thin bar in the top bar. */
export function jobProgressRatio(job: JobOut | null): number | null {
  if (!job || isTerminal(job)) return null;
  const stages = Object.values(job.stage_progress);
  if (!stages.length) return 0;
  return stages.reduce((sum, value) => sum + value, 0) / stages.length;
}

export function useIngestJobSocket(jobId: string | null): {
  job: JobOut | null;
  transport: 'socket' | 'polling' | null;
} {
  const [job, setJob] = useState<JobOut | null>(null);
  const [transport, setTransport] = useState<'socket' | 'polling' | null>(null);
  // Terminal state has to be readable from inside the poll closure without
  // re-running the effect (which would tear the socket down every frame).
  const done = useRef(false);

  useEffect(() => {
    if (!jobId) {
      setJob(null);
      setTransport(null);
      return;
    }

    done.current = false;
    let socket: WebSocket | null = null;
    let poller: number | undefined;
    let openTimer: number | undefined;
    let cancelled = false;

    const stopPolling = () => {
      if (poller !== undefined) window.clearInterval(poller);
      poller = undefined;
    };

    const accept = (next: JobOut) => {
      if (cancelled) return;
      setJob(next);
      if (next.state === 'done' || next.state === 'failed') {
        done.current = true;
        stopPolling();
      }
    };

    const startPolling = () => {
      if (cancelled || done.current || poller !== undefined) return;
      setTransport('polling');
      const tick = () => {
        void api.ingest
          .job(jobId)
          .then(accept)
          // A failed poll is not fatal: the next tick retries, and the job row
          // outlives any single request.
          .catch(() => undefined);
      };
      tick();
      poller = window.setInterval(tick, POLL_MS);
    };

    const connect = (token: string) => {
      if (cancelled) return;
      socket = new WebSocket(socketUrl(jobId, token));

      // If it has not opened by then, stop waiting and poll. The socket is
      // left connecting: if it does come up, onopen cancels the poll.
      openTimer = window.setTimeout(startPolling, OPEN_GRACE_MS);

      socket.onopen = () => {
        if (cancelled) return;
        window.clearTimeout(openTimer);
        stopPolling();
        setTransport('socket');
      };
      socket.onmessage = (event: MessageEvent<string>) => {
        try {
          accept(JSON.parse(event.data) as JobOut);
        } catch {
          // A frame we cannot parse is not worth killing the socket over.
        }
      };
      socket.onerror = startPolling;
      socket.onclose = (event: CloseEvent) => {
        window.clearTimeout(openTimer);
        if (cancelled || done.current) return;
        if (event.code === WS_POLICY_VIOLATION) {
          // The access token expired mid-job. One refresh, one reconnect —
          // not a loop, because a second rejection falls through to polling,
          // which goes through the fetch client and its own refresh handling.
          void refreshSession()
            .then((token) => connect(token))
            .catch(startPolling);
          return;
        }
        startPolling();
      };
    };

    const token = getAccessToken();
    if (token) connect(token);
    else void refreshSession().then(connect).catch(startPolling);

    return () => {
      cancelled = true;
      window.clearTimeout(openTimer);
      stopPolling();
      // 1000 so the server logs a clean disconnect rather than an error.
      socket?.close(1000);
    };
  }, [jobId]);

  return { job, transport };
}
