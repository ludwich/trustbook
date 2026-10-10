// Types mirror the API contract in docs/ARCHITECTURE.md exactly. Keep them in sync.

// ---- Health and setup -------------------------------------------------------

export interface Health {
  status: "ok";
  version: string;
}

export interface SetupStatus {
  completed: boolean;
  chat_ready: boolean;
}

// ---- Settings ---------------------------------------------------------------

export type Power = "low" | "normal" | "high";

export interface Settings {
  power: Power;
  setup_completed: boolean;
  /** Effective worker count for the current power setting. */
  workers: number;
  /** Real (capped) worker count per power level. Phase 2; absent on older backends. */
  workers_by_power?: Record<Power, number>;
  thresholds: { refuse: number; verify: number };
  /** One-time hints the user has dismissed, e.g. "chat_grounding". */
  hints_dismissed: string[];
}

export interface SettingsUpdate {
  power?: Power;
  /** Marks a one-time hint as dismissed. */
  dismiss_hint?: string;
}

// ---- Capabilities -----------------------------------------------------------

export type CapabilityId =
  "chat" | "gate" | "embeddings" | "reranker" | "grounding" | "stt" | "web_search" | "tts";

export type CapabilityKind = "endpoint" | "bundled" | "bundled_with_endpoint_override";
export type CapabilityStatus = "ready" | "not_configured" | "error" | "disabled";
export type Locality = "local" | "remote";
export type FieldType = "text" | "url" | "password" | "select" | "toggle";
export type AdapterId = "openai_compatible" | "azure_openai" | "anthropic" | "mock";
export type SearchProvider = "searxng" | "brave" | "tavily";
export type SttMode = "bundled" | "endpoint";

export interface Option {
  value: string;
  label: string;
}

export interface Field {
  name: string;
  label: string;
  type: FieldType;
  required: boolean;
  placeholder?: string;
  help?: string;
  options?: Option[];
}

export interface Preset {
  id: string;
  label: string;
  adapter: AdapterId;
  base_url: string | null;
  needs_api_key: boolean;
}

export interface CapabilityConfig {
  enabled: boolean;
  preset: string | null;
  base_url: string | null;
  model: string | null;
  api_version: string | null;
  api_key_set: boolean;
  /** For example "…a1b2". Never the key itself. */
  api_key_hint: string | null;
  /** gate only: reuse the chat config. */
  use_chat: boolean | null;
  /** web_search only. */
  provider: SearchProvider | null;
  /** stt only. */
  mode: SttMode | null;
}

export interface CapabilityConfigUpdate {
  enabled?: boolean;
  preset?: string | null;
  base_url?: string | null;
  model?: string | null;
  api_version?: string | null;
  /** "" clears the saved key; omit to keep it. */
  api_key?: string;
  use_chat?: boolean | null;
  provider?: SearchProvider | null;
  mode?: SttMode | null;
}

export interface TestResult {
  ok: boolean;
  message: string;
  latency_ms: number | null;
  /** ISO 8601 UTC */
  tested_at: string;
}

export interface BundledInfo {
  model_id: string;
  revision: string;
  license: string;
  present: boolean;
  fallback: boolean;
}

export interface Capability {
  id: CapabilityId;
  label: string;
  description: string;
  kind: CapabilityKind;
  required: boolean;
  hidden: boolean;
  contract: string;
  fields: Field[];
  presets: Preset[];
  providers: Option[];
  config: CapabilityConfig;
  locality: Locality | null;
  status: CapabilityStatus;
  status_detail: string;
  last_test: TestResult | null;
  bundled: BundledInfo | null;
}

export interface ModelList {
  models: string[];
}

// ---- Spaces -----------------------------------------------------------------

export interface Space {
  id: string;
  slug: string;
  name: string;
  description: string;
  created_at: string;
  schema_version: number;
  embedding: { model: string; revision: string };
  /** Container path, e.g. /data/spaces/acme-audit */
  folder: string;
  source_count: number;
}

export interface SpaceCreate {
  name: string;
  description?: string;
}

export interface SpaceUpdate {
  name?: string;
  description?: string;
}

// ---- Jobs -------------------------------------------------------------------

export type JobStatus = "queued" | "running" | "paused" | "done" | "failed" | "cancelled";

export interface Job {
  id: string;
  kind: string;
  title: string;
  space_id: string | null;
  space_name: string | null;
  status: JobStatus;
  progress: number | null;
  position: number;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface QueueSummary {
  queued: number;
  running: number;
  paused: number;
  eta_seconds: number | null;
  workers: number;
}

export interface JobList {
  jobs: Job[];
  summary: QueueSummary;
}

// ---- Request log ------------------------------------------------------------

export interface RequestLogEntry {
  id: number;
  timestamp: string;
  capability: string;
  endpoint_host: string;
  space_id: string | null;
  space_name: string | null;
  bytes_sent: number;
  tokens_sent: number | null;
  excerpt: string;
}

// ---- Calibration ------------------------------------------------------------

export type CalibrationStatus = "pending" | "running" | "done" | "failed";

export interface Calibration {
  version: number;
  measured_at: string;
  cpu_count: number;
  embedding_chunks_per_s: number | null;
  rerank_pairs_per_s: number | null;
  parse_pages_per_s: number | null;
  stt_realtime_factor: number | null;
  notes: string[];
  /** Calibration v3+. Absent in older saved results. */
  cpu?: CalibrationCpu | null;
  /** Calibration v3+. Absent in older saved results. */
  runtime?: CalibrationRuntime | null;
}

export interface CalibrationCpu {
  model: string | null;
  logical: number;
  usable: number;
  physical: number | null;
  bf16: boolean;
}

export type ModelDtype = "float32" | "bfloat16";

export interface CalibrationRuntime {
  torch_threads: number | null;
  embedding_dtype: ModelDtype | null;
  reranker_dtype: ModelDtype | null;
}

export interface CalibrationState {
  status: CalibrationStatus;
  results: Calibration | null;
}

// ---- Phase 1: sources -------------------------------------------------------

export type SourceType = "upload" | "folder" | "url" | "youtube" | "search" | "paste";
export type SourceGroup = "documents" | "transcripts" | "web" | "code";
export type SourceStatus = "queued" | "reading" | "indexing" | "ready" | "failed";

export interface SourceStats {
  pages?: number;
  files?: number;
  duration_s?: number;
  chunks?: number;
  chars?: number;
  /** Files left out of a linked folder because gitleaks found possible secrets. */
  skipped_secrets?: number;
}

export interface Source {
  id: string;
  type: SourceType;
  title: string;
  origin: string | null;
  group: SourceGroup;
  status: SourceStatus;
  progress: number | null;
  failure_reason: string | null;
  failure_next_step: string | null;
  quality_warning: string | null;
  enabled: boolean;
  snapshot_at: string | null;
  commit_hash: string | null;
  stats: SourceStats;
  created_at: string;
  job_id: string | null;
}

export interface PasteSourceCreate {
  title?: string;
  text: string;
}

export interface SourceUpdate {
  enabled?: boolean;
  title?: string;
}

export interface WebSearchResult {
  title: string;
  url: string;
  snippet: string;
}

export interface WebSearchResponse {
  results: WebSearchResult[];
}

export interface SearchItem {
  url: string;
  title: string;
}

/** Where a chunk came from. Which keys are present depends on the source kind. */
export interface Locator {
  pages?: number[];
  start_s?: number;
  end_s?: number;
  speaker?: string | null;
  heading?: string | null;
  offset?: number;
  file?: string;
  start_line?: number;
  end_line?: number;
  symbol?: string | null;
}

export interface Chunk {
  id: string;
  source_id: string;
  source_title: string;
  text: string;
  start_offset: number;
  end_offset: number;
  locator: Locator;
}

export interface SnapshotSegment {
  start: number;
  end: number;
  page?: number;
  start_s?: number;
  end_s?: number;
  speaker?: string;
  heading?: string;
  file?: string;
  start_line?: number;
  end_line?: number;
}

export interface SnapshotMap {
  version: number;
  kind: "pages" | "timestamps" | "sections" | "files";
  segments: SnapshotSegment[];
}

export interface Snapshot {
  markdown: string;
  map: SnapshotMap;
}

/** One gitleaks finding. Never contains the secret value. */
export interface SecretFinding {
  file: string;
  line: number;
  rule_id: string;
}

/** GET …/sources/{id}/secrets (404 when the source has no report). */
export interface SecretsReport {
  files_skipped: number;
  findings: SecretFinding[];
}

export interface CodeContext {
  file: string;
  commit: string | null;
  language: string | null;
  /** 1-based line number of `lines[0]`. */
  first_line: number;
  /** The whole file, or a window around the cited lines for long files. */
  lines: string[];
  /** Inclusive 1-based line range of the cited chunk. */
  highlight: [number, number];
}

export interface TextContext {
  /** "Page 3", "Pages 3–4", "Section: Install", "01:02–01:45". */
  label: string | null;
  before: string;
  highlight: string;
  after: string;
}

/** GET …/chunks/{id}/context: everything the source viewer shows. */
export interface ChunkContext {
  chunk: Chunk;
  source_type: string;
  view: "code" | "text";
  code?: CodeContext;
  text?: TextContext;
}

export interface BrowseEntry {
  name: string;
  path: string;
  is_dir: boolean;
  size: number | null;
}

export interface BrowseListing {
  /** Relative to the read-only browse root; "" is the root. */
  path: string;
  parent: string | null;
  entries: BrowseEntry[];
}

// ---- Phase 1: chat ----------------------------------------------------------

export interface ChatStatus {
  /** Sources that are both ticked and ready (the X in "Using X of Y sources"). */
  enabled_sources: number;
  /** Sources that are ready (the Y). */
  ready_sources: number;
  total_sources: number;
  chat_ready: boolean;
}

export interface Conversation {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface ConversationCreate {
  title?: string;
}

export interface Citation {
  /** Per answer, in order of first use. */
  n: number;
  chunk_id: string;
  source_id: string;
  source_title: string;
  locator: Locator;
}

export interface AnswerSentence {
  text: string;
  citations: Citation[];
}

export type MessageBadge = "verified" | "partially_verified" | "refused" | "unverified";

export interface MessageContent {
  /** User messages; also the refusal text on refused answers. */
  text?: string;
  sentences?: AnswerSentence[];
  /** Refused answers: what to add. Not in the contract yet (see the Phase 1 frontend notes). */
  suggestion?: string | null;
}

export interface Message {
  id: string;
  role: "user" | "assistant";
  created_at: string;
  content: MessageContent;
  badge: MessageBadge | null;
  removed_sentences: string[];
  sources_used: { source_id: string; title: string }[];
}

export type ChatStage = "retrieving" | "generating" | "checking";
export type RefusalReason = "not_in_sources" | "no_sources" | "invalid_output";

export interface Refusal {
  reason: RefusalReason;
  message: string;
  suggestion: string | null;
}

/** Events of the answer stream (POST …/messages, text/event-stream). */
export type ChatStreamEvent =
  | { type: "status"; stage: ChatStage }
  | { type: "sentence"; index: number; text: string; citations: Citation[] }
  | ({ type: "refused" } & Refusal)
  | { type: "done"; message: Message }
  | { type: "error"; message: string };

// ---- Phase 1: overview ------------------------------------------------------

export type OverviewStatus = "none" | "pending" | "ready" | "failed";

export interface Overview {
  enabled: boolean;
  status: OverviewStatus;
  sentences: AnswerSentence[];
  questions: string[];
  generated_at: string | null;
  /** Sources changed since it was written. */
  stale: boolean;
  /** Present when the chat endpoint is remote. */
  estimate: { tokens: number; remote: boolean } | null;
}
