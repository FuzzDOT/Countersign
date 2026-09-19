/**
 * Response and request shapes for /api/v1.
 *
 * This file mirrors `backend/api/v1/schemas.py` one-to-one. The brief (§4.1)
 * wants types generated from the live OpenAPI schema; run `make types` to
 * produce `schema.d.ts`, and if the backend contract moves, diff it against
 * this file and update here. Everything in the app imports from THIS module,
 * so swapping the source of truth later is a one-file change.
 *
 * Nullable means nullable: `trust.fragility === null` is "not yet measured",
 * never zero.
 */

export type UUID = string;
export type ISODateTime = string;

export type RoutingBucket = 'auto_file' | 'flag_for_review' | 'escalate_now';
export type Resolver = 'classical' | 'nemotron';
export type UserRole = 'owner' | 'analyst' | 'viewer';
export type DocSource =
  | 'invoice'
  | 'email'
  | 'press_release'
  | 'rss'
  | 'gdelt'
  | 'note'
  | 'transaction_log';

export type RelationType =
  | 'WIRED_FUNDS_TO'
  | 'OWNED_BY'
  | 'INVOICED'
  | 'SHARES_ADDRESS_WITH'
  | 'SIGNATORY_OF'
  | 'NO_RELATION';

export type EntityType =
  | 'ORG'
  | 'PERSON'
  | 'MONEY'
  | 'DATE'
  | 'ACCOUNT_REF'
  | 'TRANSACTION_TYPE';

/** Permission strings from GET /auth/me. Gate on these, never on `role`. */
export type Permission =
  | 'insights:read'
  | 'graph:read'
  | 'documents:upload'
  | 'ablation:run'
  | 'voice:use'
  | 'evals:read'
  | 'calibration:run'
  | 'users:manage';

// ── envelope ────────────────────────────────────────────────────────────────

export interface PaginationOut {
  cursor?: string | null;
  next_cursor?: string | null;
  has_more: boolean;
  limit: number;
}

export interface Paginated<T> {
  data: T[];
  pagination: PaginationOut;
}

export interface ErrorBody {
  code: string;
  message: string;
  status: number;
  request_id: string;
  details: Record<string, unknown>;
}

export interface ErrorEnvelope {
  error: ErrorBody;
}

// ── auth ────────────────────────────────────────────────────────────────────

export interface UserOut {
  id: UUID;
  email: string;
  role: UserRole;
  org_id: UUID;
}

export interface AuthResponse {
  user: UserOut;
  access_token: string;
  token_type: 'bearer';
  expires_in: number;
}

export interface MeResponse {
  id: UUID;
  email: string;
  role: UserRole;
  org_id: UUID;
  org_name: string;
  permissions: string[];
}

// ── documents ───────────────────────────────────────────────────────────────

export interface MentionOut {
  entity_id: UUID;
  surface: string;
  entity_type: string;
  char_start: number;
  char_end: number;
  tagger_conf: number;
}

export interface SpanOut {
  insight_id: UUID;
  char_start: number;
  char_end: number;
  relation: string;
  confidence: number;
  routing: RoutingBucket;
}

export interface DocumentDetail {
  id: UUID;
  title: string;
  source: DocSource;
  received_at: ISODateTime;
  raw_text: string;
  spans: SpanOut[];
  mentions: MentionOut[];
}

// ── ingest ──────────────────────────────────────────────────────────────────

export type JobStateName =
  | 'queued'
  | 'tagging'
  | 'parsing'
  | 'relating'
  | 'scoring'
  | 'routing'
  | 'done'
  | 'failed';

export interface JobOut {
  id: UUID;
  state: JobStateName;
  docs_total: number;
  docs_done: number;
  insights_found: number;
  stage_progress: Record<'tagging' | 'parsing' | 'relating' | 'scoring' | 'routing', number>;
  started_at?: ISODateTime | null;
  finished_at?: ISODateTime | null;
  error?: string | null;
}

// ── insights ────────────────────────────────────────────────────────────────

export interface EntityRef {
  id: UUID;
  canonical: string;
  entity_type: string;
}

export interface Citation {
  document_id: UUID;
  document_title: string;
  char_start: number;
  char_end: number;
  sentence_text: string;
}

export interface TrustScores {
  confidence: number;
  vacuity: number;
  dissonance: number;
  /** null until the fuzzer has run. Null is not zero. */
  fragility: number | null;
}

export interface NemotronInfo {
  run_id: UUID;
  rationale: string;
  latency_ms: number;
}

export interface InsightOut {
  id: UUID;
  relation: string;
  subject: EntityRef;
  object: EntityRef;
  citation: Citation;
  trust: TrustScores;
  routing: RoutingBucket;
  resolved_by: Resolver;
  nemotron?: NemotronInfo | null;
  degraded: boolean;
  attention_available: boolean;
  created_at: ISODateTime;
}

export interface AttentionEdge {
  edge_id: string;
  src_token: string;
  dst_token: string;
  weight: number;
  src_idx: number;
  dst_idx: number;
}

export interface GraphNeighborhood {
  node_ids: UUID[];
  depth: number;
}

export interface AblationHistoryItem {
  id: UUID;
  masked_edges: string[];
  confidence_before: number;
  confidence_after: number;
  load_bearing: boolean;
  created_at: ISODateTime;
}

export type Perturbation = 'synonym' | 'rename' | 'boilerplate' | 'reorder' | 'punctuation';

export interface FragilityTrialOut {
  perturbation: Perturbation;
  label_flipped: boolean;
  conf_delta: number;
  relation_lost: boolean;
}

export interface InsightDetail extends InsightOut {
  attention: AttentionEdge[];
  tokens: string[];
  graph_neighborhood: GraphNeighborhood;
  ablation_history: AblationHistoryItem[];
  fragility_trials: FragilityTrialOut[];
}

export interface InsightStats {
  total: number;
  by_routing: Partial<Record<RoutingBucket, number>>;
  by_resolver: Partial<Record<Resolver, number>>;
  mean_confidence: number;
  high_vacuity_count: number;
  documents_ingested: number;
}

export type InsightSort =
  | 'created_at'
  | '-created_at'
  | 'confidence'
  | '-confidence'
  | 'vacuity'
  | '-vacuity'
  | 'fragility'
  | '-fragility';

// ── graph ───────────────────────────────────────────────────────────────────

export interface GraphNode {
  id: UUID;
  canonical: string;
  entity_type: string;
  mention_count: number;
  degree: number;
  risk: number;
  flags: string[];
}

export interface GraphEdge {
  id: UUID;
  source: UUID;
  target: UUID;
  relation: string;
  confidence: number;
  vacuity: number;
  routing: RoutingBucket;
  insight_ids: UUID[];
  weight: number;
}

export interface GraphCycle {
  node_ids: UUID[];
  length: number;
  relation: string;
  risk: number;
}

export interface GraphResponse {
  nodes: GraphNode[];
  edges: GraphEdge[];
  cycles: GraphCycle[];
  truncated: boolean;
}

export interface NeighborOut {
  entity: GraphNode;
  relation: string;
  direction: 'in' | 'out';
  confidence: number;
  insight_ids: UUID[];
}

export interface EntityDocument {
  id: UUID;
  title: string;
  mention_count: number;
}

export interface EntityDetail {
  entity: GraphNode & { first_seen: ISODateTime };
  aliases: string[];
  neighbors: NeighborOut[];
  documents: EntityDocument[];
  insight_count: number;
}

// ── ablation ────────────────────────────────────────────────────────────────

export type AblationMode = 'zero' | 'uniform';

export interface AblationRequest {
  masked_edges: string[];
  mode: AblationMode;
}

export interface AblationState {
  confidence: number;
  vacuity: number;
  routing: RoutingBucket;
  relation: string;
}

export interface AblationResponse {
  run_id: UUID;
  insight_id: UUID;
  masked_edges: string[];
  before: AblationState;
  after: AblationState;
  delta: { confidence: number; vacuity: number; routing_changed: boolean };
  load_bearing: boolean;
  /** Server-templated. Render as-is. */
  interpretation: string;
  latency_ms: number;
}

// ── routing / evals ─────────────────────────────────────────────────────────

export interface NemotronRunOut {
  id: UUID;
  insight_id: UUID | null;
  prompt_sha: string;
  decision: RoutingBucket;
  rationale: string;
  latency_ms: number;
  input_tokens?: number | null;
  output_tokens?: number | null;
  degraded: boolean;
  created_at: ISODateTime;
}

export interface Correlation {
  spearman: number;
  pearson: number;
  p_value: number;
}

export interface ScatterPoint {
  insight_id: UUID;
  vacuity: number;
  fragility: number;
  routing: RoutingBucket;
}

export interface PerturbationStat {
  perturbation: string;
  flip_rate: number;
  mean_abs_conf_delta: number;
  relation_loss_rate: number;
}

export interface QuartileRow {
  vacuity_quartile: number;
  vacuity_range: [number, number];
  mean_fragility: number;
  flip_rate: number;
}

export interface FragilityEval {
  n_insights: number;
  n_trials: number;
  perturbations: string[];
  correlation: Correlation;
  scatter: ScatterPoint[];
  by_perturbation: PerturbationStat[];
  quartile_table: QuartileRow[];
  interpretation: string;
}

export interface PerClassMetrics {
  bucket: RoutingBucket;
  precision: number;
  recall: number;
  f1: number;
  support: number;
}

export interface CascadeBaseline {
  classical_only_accuracy: number;
  nemotron_on_everything_accuracy: number;
  cascade_accuracy: number;
  cascade_llm_calls: number;
  nemotron_on_everything_llm_calls: number;
  interpretation: string;
}

export interface DocumentedFailure {
  case_id: UUID;
  insight_id: UUID | null;
  ground_truth: RoutingBucket;
  predicted: RoutingBucket;
  sentence_text: string;
  note: string;
}

export interface RoutingEval {
  n_cases: number;
  confusion_matrix: { labels: string[]; matrix: number[][] };
  per_class: PerClassMetrics[];
  macro_f1: number;
  accuracy: number;
  cascade_baseline: CascadeBaseline;
  documented_failures: DocumentedFailure[];
}

export interface CalibrationBin {
  bin_lo: number;
  bin_hi: number;
  avg_conf: number;
  accuracy: number;
  count: number;
}

export interface CalibrationSnapshot {
  id: UUID;
  label: 'baseline' | 'post_recalibration';
  temperature: number;
  ece: number;
  mce: number;
  brier: number;
  bins: CalibrationBin[];
  created_at: ISODateTime;
}

export interface CalibrationEval {
  snapshots: CalibrationSnapshot[];
  current_snapshot_id: UUID | null;
  hard_negatives_logged: number;
}

export interface CalibrationMetrics {
  temperature: number;
  ece: number;
  mce: number;
  brier: number;
  bins: CalibrationBin[];
}

export interface RecalibrateResponse {
  before: CalibrationMetrics;
  after: CalibrationMetrics;
  improvement: { ece_absolute: number; ece_relative: number };
  n_hard_negatives: number;
  elapsed_ms: number;
  snapshot_id: UUID;
}

// ── voice ───────────────────────────────────────────────────────────────────

export type BriefingScope = 'flagged' | 'escalated' | 'all_new';

export interface BriefingRequest {
  scope: BriefingScope;
  max_items: number;
  voice_id?: string;
}

export interface TranscriptSegment {
  segment_id: string;
  start_ms: number;
  end_ms: number;
  text: string;
  insight_id?: UUID | null;
}

export interface BriefingResponse {
  briefing_id: UUID;
  audio_url: string;
  duration_ms: number;
  transcript: TranscriptSegment[];
  insight_ids: UUID[];
  generated_at: ISODateTime;
  is_fallback: boolean;
}

export type VoiceIntent =
  | 'explain_flag'
  | 'show_source'
  | 'list_flagged'
  | 'entity_summary'
  | 'confidence_query'
  | 'dismiss'
  | 'unknown';

export interface AskResponse {
  question_id: UUID;
  heard: string;
  stt_confidence: number;
  intent: VoiceIntent;
  resolved_insight_id: UUID | null;
  answer_text: string;
  citation: Citation | null;
  ablation_run_id: UUID | null;
  audio_url: string | null;
  duration_ms: number;
}
