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
  thresholds: { refuse: number; verify: number };
}

export interface SettingsUpdate {
  power?: Power;
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
