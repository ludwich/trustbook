import type {
  CalibrationState,
  Capability,
  CapabilityConfigUpdate,
  CapabilityId,
  Health,
  Job,
  JobList,
  ModelList,
  RequestLogEntry,
  Settings,
  SettingsUpdate,
  SetupStatus,
  Space,
  SpaceCreate,
  SpaceUpdate,
  TestResult,
  BrowseListing,
  ChatStage,
  ChatStatus,
  ChatStreamEvent,
  Chunk,
  ChunkContext,
  Citation,
  Conversation,
  ConversationCreate,
  Message,
  Overview,
  PasteSourceCreate,
  RefusalReason,
  SearchItem,
  SecretsReport,
  Snapshot,
  Source,
  SourceUpdate,
  WebSearchResponse,
  GateDecision,
  GateLogEntry,
  SourceEstimate,
  SourceEstimateItem,
} from "./types";
import { readSse, type SseEvent } from "@/lib/sse";

/** Error thrown for any non-2xx response. `message` is plain English, safe to show. */
export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

type Method = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

function baseUrl(): string {
  // Absolute URL so it also works under Node's fetch in tests; same-origin in the browser.
  return typeof window !== "undefined" ? window.location.origin : "http://localhost";
}

/** Turns FastAPI's `{"detail": ...}` (string or validation list) into one readable line. */
export function detailToMessage(detail: unknown, fallback: string): string {
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    const parts = detail
      .map((d: unknown) => {
        if (d && typeof d === "object" && "msg" in d && typeof d.msg === "string") {
          const loc =
            "loc" in d && Array.isArray(d.loc)
              ? d.loc.filter((p: unknown) => p !== "body").join(".")
              : "";
          return loc ? `${loc}: ${d.msg}` : d.msg;
        }
        return null;
      })
      .filter((p): p is string => Boolean(p));
    if (parts.length) return parts.join("; ");
  }
  return fallback;
}

function statusFallback(status: number): string {
  if (status === 404) return "Not found.";
  if (status === 409) return "That can't be done right now.";
  if (status === 422) return "Some values are not valid.";
  if (status === 502) return "The endpoint could not be reached.";
  if (status >= 500) return "Trustbook had a problem. Try again.";
  return `Request failed (${status}).`;
}

async function send(path: string, init: RequestInit): Promise<Response> {
  let res: Response;
  try {
    res = await fetch(new URL(path, baseUrl()), init);
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    throw new ApiError(0, "Can't reach Trustbook. Is the app running?");
  }
  if (!res.ok) {
    let detail: unknown = undefined;
    try {
      detail = ((await res.json()) as { detail?: unknown }).detail;
    } catch {
      // Body was not JSON; fall back to a status-based message.
    }
    throw new ApiError(res.status, detailToMessage(detail, statusFallback(res.status)));
  }
  return res;
}

async function parseJson<T>(res: Response): Promise<T> {
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

export async function request<T>(method: Method, path: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method, headers: { Accept: "application/json" } };
  if (body !== undefined) {
    init.headers = { ...init.headers, "Content-Type": "application/json" };
    init.body = JSON.stringify(body);
  }
  return parseJson<T>(await send(path, init));
}

/** POST multipart/form-data (the browser sets the boundary header). */
async function postForm<T>(path: string, form: FormData): Promise<T> {
  return parseJson<T>(
    await send(path, { method: "POST", headers: { Accept: "application/json" }, body: form }),
  );
}

function parseEventData(data: string): Record<string, unknown> | null {
  try {
    const v: unknown = JSON.parse(data);
    return v && typeof v === "object" ? (v as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

/** Maps one SSE event of the answer stream to a typed event. Unknown or malformed → null. */
export function toChatEvent(ev: SseEvent): ChatStreamEvent | null {
  const d = parseEventData(ev.data);
  if (!d) return null;
  switch (ev.event) {
    case "status":
      return { type: "status", stage: d.stage as ChatStage };
    case "sentence":
      return {
        type: "sentence",
        index: typeof d.index === "number" ? d.index : 0,
        text: String(d.text ?? ""),
        citations: Array.isArray(d.citations) ? (d.citations as Citation[]) : [],
      };
    case "refused":
      return {
        type: "refused",
        reason: (d.reason as RefusalReason) ?? "not_in_sources",
        message: String(d.message ?? ""),
        suggestion: typeof d.suggestion === "string" ? d.suggestion : null,
      };
    case "done":
      return d.message && typeof d.message === "object"
        ? { type: "done", message: d.message as Message }
        : null;
    case "error":
      return { type: "error", message: String(d.message ?? "") };
    default:
      return null;
  }
}

/**
 * Sends a question and yields the answer stream's events.
 * Throws ApiError if the request is refused (non-2xx) before streaming starts.
 */
export async function* streamMessage(
  spaceId: string,
  conversationId: string,
  content: string,
  signal?: AbortSignal,
): AsyncGenerator<ChatStreamEvent> {
  const res = await send(
    `/api/spaces/${enc(spaceId)}/conversations/${enc(conversationId)}/messages`,
    {
      method: "POST",
      headers: { Accept: "text/event-stream", "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
      signal,
    },
  );
  if (!res.body) throw new ApiError(0, "The answer could not be read.");
  for await (const ev of readSse(res.body, signal)) {
    const typed = toChatEvent(ev);
    if (typed) yield typed;
  }
}

const GATE_DECISIONS: readonly GateDecision[] = ["sources", "brainstorm", "chitchat"];

function asDecision(v: unknown): GateDecision | null {
  return typeof v === "string" && (GATE_DECISIONS as readonly string[]).includes(v)
    ? (v as GateDecision)
    : null;
}

function firstString(...values: unknown[]): string {
  for (const v of values) if (typeof v === "string") return v;
  return "";
}

/**
 * One gate log row from the server. Accepts the documented-in-spirit names (`created_at`,
 * `question`, `correction`) and the DB column names (`created`, `input`, `user_correction`).
 */
export function toGateLogEntry(raw: unknown): GateLogEntry | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  if (typeof r.id !== "number") return null;
  return {
    id: r.id,
    created_at: firstString(r.created_at, r.created, r.timestamp),
    question: firstString(r.question, r.input),
    decision: asDecision(r.decision) ?? "sources",
    confidence: typeof r.confidence === "number" ? r.confidence : null,
    correction: asDecision(r.correction ?? r.user_correction),
  };
}

/** The gate log may come as a bare list or wrapped as `{"entries": [...]}`. */
export function toGateLog(raw: unknown): GateLogEntry[] {
  const list: unknown[] = Array.isArray(raw)
    ? raw
    : raw && typeof raw === "object" && Array.isArray((raw as { entries?: unknown }).entries)
      ? (raw as { entries: unknown[] }).entries
      : [];
  return list.map(toGateLogEntry).filter((e): e is GateLogEntry => e !== null);
}

function enc(v: string): string {
  return encodeURIComponent(v);
}

export const api = {
  // Health and setup
  health: () => request<Health>("GET", "/api/health"),
  getSetup: () => request<SetupStatus>("GET", "/api/setup"),
  completeSetup: () => request<SetupStatus>("POST", "/api/setup/complete"),

  // Settings
  getSettings: () => request<Settings>("GET", "/api/settings"),
  updateSettings: (body: SettingsUpdate) => request<Settings>("PUT", "/api/settings", body),

  // Capabilities
  listCapabilities: () => request<Capability[]>("GET", "/api/capabilities"),
  getCapability: (id: CapabilityId) => request<Capability>("GET", `/api/capabilities/${enc(id)}`),
  updateCapability: (id: CapabilityId, body: CapabilityConfigUpdate) =>
    request<Capability>("PUT", `/api/capabilities/${enc(id)}`, body),
  /** Tests the saved config merged with the optional unsaved draft. */
  testCapability: (id: CapabilityId, draft: CapabilityConfigUpdate = {}) =>
    request<TestResult>("POST", `/api/capabilities/${enc(id)}/test`, draft),
  listModels: (id: CapabilityId, draft: CapabilityConfigUpdate = {}) =>
    request<ModelList>("POST", `/api/capabilities/${enc(id)}/models`, draft),

  // Spaces
  listSpaces: () => request<Space[]>("GET", "/api/spaces"),
  createSpace: (body: SpaceCreate) => request<Space>("POST", "/api/spaces", body),
  getSpace: (id: string) => request<Space>("GET", `/api/spaces/${enc(id)}`),
  updateSpace: (id: string, body: SpaceUpdate) =>
    request<Space>("PATCH", `/api/spaces/${enc(id)}`, body),
  deleteSpace: (id: string) => request<void>("DELETE", `/api/spaces/${enc(id)}`),
  setActiveSpace: (spaceId: string | null) =>
    request<void>("PUT", "/api/active-space", { space_id: spaceId }),

  // Jobs
  listJobs: () => request<JobList>("GET", "/api/jobs"),
  cancelJob: (id: string) => request<Job>("POST", `/api/jobs/${enc(id)}/cancel`),
  pauseJob: (id: string) => request<Job>("POST", `/api/jobs/${enc(id)}/pause`),
  resumeJob: (id: string) => request<Job>("POST", `/api/jobs/${enc(id)}/resume`),
  reorderJobs: (jobIds: string[]) =>
    request<void>("POST", "/api/jobs/reorder", { job_ids: jobIds }),

  // Request log
  listRequestLog: (opts: { limit?: number; beforeId?: number } = {}) => {
    const q = new URLSearchParams();
    q.set("limit", String(opts.limit ?? 100));
    if (opts.beforeId !== undefined) q.set("before_id", String(opts.beforeId));
    return request<RequestLogEntry[]>("GET", `/api/request-log?${q.toString()}`);
  },
  clearRequestLog: () => request<void>("DELETE", "/api/request-log"),

  // Calibration
  getCalibration: () => request<CalibrationState>("GET", "/api/calibration"),
  runCalibration: () => request<Job>("POST", "/api/calibration/run"),

  // Sources
  listSources: (sid: string) => request<Source[]>("GET", `/api/spaces/${enc(sid)}/sources`),
  addPaste: (sid: string, body: PasteSourceCreate) =>
    request<Source>("POST", `/api/spaces/${enc(sid)}/sources/paste`, body),
  uploadFiles: (sid: string, files: File[]) => {
    const form = new FormData();
    for (const f of files) form.append("files", f, f.name);
    return postForm<Source[]>(`/api/spaces/${enc(sid)}/sources/upload`, form);
  },
  addUrl: (sid: string, url: string) =>
    request<Source>("POST", `/api/spaces/${enc(sid)}/sources/url`, { url }),
  addFolder: (sid: string, path: string) =>
    request<Source>("POST", `/api/spaces/${enc(sid)}/sources/folder`, { path }),
  webSearch: (sid: string, query: string, limit?: number) =>
    request<WebSearchResponse>(
      "POST",
      `/api/spaces/${enc(sid)}/web-search`,
      limit === undefined ? { query } : { query, limit },
    ),
  addFromSearch: (sid: string, items: SearchItem[]) =>
    request<Source[]>("POST", `/api/spaces/${enc(sid)}/sources/from-search`, { items }),
  updateSource: (sid: string, sourceId: string, body: SourceUpdate) =>
    request<Source>("PATCH", `/api/spaces/${enc(sid)}/sources/${enc(sourceId)}`, body),
  setSourcesEnabled: (sid: string, sourceIds: string[], enabled: boolean) =>
    request<Source[]>("PUT", `/api/spaces/${enc(sid)}/sources/enabled`, {
      source_ids: sourceIds,
      enabled,
    }),
  refreshSource: (sid: string, sourceId: string) =>
    request<Source>("POST", `/api/spaces/${enc(sid)}/sources/${enc(sourceId)}/refresh`),
  deleteSource: (sid: string, sourceId: string) =>
    request<void>("DELETE", `/api/spaces/${enc(sid)}/sources/${enc(sourceId)}`),
  getSnapshot: (sid: string, sourceId: string) =>
    request<Snapshot>("GET", `/api/spaces/${enc(sid)}/sources/${enc(sourceId)}/snapshot`),
  /** URL of an uploaded original (for links; not fetched as JSON). */
  originalUrl: (sid: string, sourceId: string) =>
    `/api/spaces/${enc(sid)}/sources/${enc(sourceId)}/original`,
  getChunk: (sid: string, chunkId: string) =>
    request<Chunk>("GET", `/api/spaces/${enc(sid)}/chunks/${enc(chunkId)}`),
  getChunkContext: (sid: string, chunkId: string) =>
    request<ChunkContext>("GET", `/api/spaces/${enc(sid)}/chunks/${enc(chunkId)}/context`),
  /** Gitleaks report of a linked folder: file, line and rule ID only. */
  getSecrets: (sid: string, sourceId: string) =>
    request<SecretsReport>("GET", `/api/spaces/${enc(sid)}/sources/${enc(sourceId)}/secrets`),
  /** How long adding these would take on this computer (SPEC §11). */
  estimateSources: (sid: string, items: SourceEstimateItem[]) =>
    request<SourceEstimate>("POST", `/api/spaces/${enc(sid)}/sources/estimate`, { items }),
  browse: (path: string) => request<BrowseListing>("GET", `/api/browse?path=${enc(path)}`),

  // Chat
  getChatStatus: (sid: string) => request<ChatStatus>("GET", `/api/spaces/${enc(sid)}/chat/status`),
  listConversations: (sid: string) =>
    request<Conversation[]>("GET", `/api/spaces/${enc(sid)}/conversations`),
  createConversation: (sid: string, body: ConversationCreate = {}) =>
    request<Conversation>("POST", `/api/spaces/${enc(sid)}/conversations`, body),
  deleteConversation: (sid: string, cid: string) =>
    request<void>("DELETE", `/api/spaces/${enc(sid)}/conversations/${enc(cid)}`),
  listMessages: (sid: string, cid: string) =>
    request<Message[]>("GET", `/api/spaces/${enc(sid)}/conversations/${enc(cid)}/messages`),
  streamMessage,

  // Gate log
  listGateLog: async (sid: string, limit = 100) =>
    toGateLog(await request<unknown>("GET", `/api/spaces/${enc(sid)}/gate-log?limit=${limit}`)),
  correctGateDecision: (sid: string, id: number, decision: GateDecision) =>
    request<unknown>("POST", `/api/spaces/${enc(sid)}/gate-log/${id}/correct`, { decision }),

  // Overview
  getOverview: (sid: string) => request<Overview>("GET", `/api/spaces/${enc(sid)}/overview`),
  refreshOverview: (sid: string) =>
    request<Job>("POST", `/api/spaces/${enc(sid)}/overview/refresh`),
  setOverviewEnabled: (sid: string, enabled: boolean) =>
    request<Overview>("PUT", `/api/spaces/${enc(sid)}/overview`, { enabled }),
};

export type Api = typeof api;
