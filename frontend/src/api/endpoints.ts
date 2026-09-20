import { request, requestBinary, type QueryParams } from './client';
import type {
  AblationRequest,
  AblationResponse,
  AskResponse,
  AuthResponse,
  BriefingRequest,
  BriefingResponse,
  CalibrationEval,
  DocSource,
  DocumentDetail,
  DocumentSummary,
  EntityDetail,
  FragilityEval,
  GraphResponse,
  InsightDetail,
  InsightOut,
  InsightStats,
  JobOut,
  MemberOut,
  MeResponse,
  NemotronRunOut,
  Paginated,
  RecalibrateResponse,
  RoutingEval,
  UploadResponse,
} from './types';

/** Typed wrappers over the endpoints, so components never build URLs. */

export const api = {
  auth: {
    me: () => request<MeResponse>('/auth/me'),
    login: (email: string, password: string) =>
      request<AuthResponse>('/auth/login', {
        method: 'POST',
        json: { email, password },
        auth: false,
      }),
    logout: () => request<void>('/auth/logout', { method: 'POST' }),
  },

  insights: {
    list: (query: QueryParams, cursor: string | undefined, limit = 25) =>
      request<Paginated<InsightOut>>('/insights', { query: { ...query, cursor, limit } }),
    stats: () => request<InsightStats>('/insights/stats'),
    get: (id: string) => request<InsightDetail>(`/insights/${encodeURIComponent(id)}`),
  },

  graph: {
    get: (query: QueryParams) => request<GraphResponse>('/graph', { query }),
    entity: (id: string) => request<EntityDetail>(`/graph/entities/${encodeURIComponent(id)}`),
  },

  documents: {
    get: (id: string) => request<DocumentDetail>(`/documents/${encodeURIComponent(id)}`),
    list: (cursor: string | undefined, limit = 25) =>
      request<Paginated<DocumentSummary>>('/documents', { query: { cursor, limit } }),
    /**
     * 202 with a job id; follow the job over the websocket. `source` is a
     * required form field and the server rejects anything outside DocSource —
     * it is a routing signal for the tagger, not a label.
     */
    upload: (files: readonly File[], source: DocSource) => {
      const form = new FormData();
      for (const file of files) form.append('files', file, file.name);
      form.append('source', source);
      return request<UploadResponse>('/documents', { method: 'POST', form, timeoutMs: 60_000 });
    },
  },

  ingest: {
    /** The websocket is primary; this is the 2s poll the contract notes call for. */
    job: (id: string) => request<JobOut>(`/ingest/jobs/${encodeURIComponent(id)}`),
    jobs: (limit = 10) => request<Paginated<JobOut>>('/ingest/jobs', { query: { limit } }),
  },

  org: {
    members: () => request<MemberOut[]>('/org/members'),
  },

  ablation: {
    run: (insightId: string, body: AblationRequest) =>
      request<AblationResponse>(`/ablation/insights/${encodeURIComponent(insightId)}`, {
        method: 'POST',
        json: body,
      }),
  },

  routing: {
    runs: (cursor: string | undefined, limit = 20) =>
      request<Paginated<NemotronRunOut>>('/routing/runs', { query: { cursor, limit } }),
  },

  evals: {
    fragility: () => request<FragilityEval>('/evals/fragility'),
    routing: () => request<RoutingEval>('/evals/routing'),
    calibration: () => request<CalibrationEval>('/evals/calibration'),
  },

  calibration: {
    /** The Idempotency-Key makes a double-click return the same snapshot. */
    recalibrate: (idempotencyKey: string) =>
      request<RecalibrateResponse>('/calibration/recalibrate', {
        method: 'POST',
        json: { method: 'temperature_scaling' },
        headers: { 'Idempotency-Key': idempotencyKey },
      }),
  },

  voice: {
    /** 8s ceiling: past that the caller switches to the recorded fallback. */
    briefing: (body: BriefingRequest, signal?: AbortSignal) =>
      request<BriefingResponse>('/voice/briefing', {
        method: 'POST',
        json: body,
        timeoutMs: 8000,
        signal,
      }),
    fallback: () => request<BriefingResponse>('/voice/fallback/briefing', { timeoutMs: 8000 }),
    ask: (audio: Blob, contextInsightId: string | null) => {
      const form = new FormData();
      form.append('audio', audio, audio.type.includes('webm') ? 'question.webm' : 'question.audio');
      if (contextInsightId) form.append('context_insight_id', contextInsightId);
      return request<AskResponse>('/voice/ask', { method: 'POST', form, timeoutMs: 20000 });
    },
    /** `audio_url` arrives root-relative ("/api/v1/voice/audio/x.mp3"). */
    audio: (audioUrl: string) => requestBinary(audioUrl, { raw: true, timeoutMs: 15000 }),
  },
};
