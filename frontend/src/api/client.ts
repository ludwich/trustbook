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
} from "./types";

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

export async function request<T>(method: Method, path: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method, headers: { Accept: "application/json" } };
  if (body !== undefined) {
    init.headers = { ...init.headers, "Content-Type": "application/json" };
    init.body = JSON.stringify(body);
  }

  let res: Response;
  try {
    res = await fetch(new URL(path, baseUrl()), init);
  } catch {
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

  if (res.status === 204) return undefined as T;
  const text = await res.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

const enc = encodeURIComponent;

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
};

export type Api = typeof api;
