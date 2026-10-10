import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";
import type {
  Overview,
  PasteSourceCreate,
  SearchItem,
  Source,
  Capability,
  CapabilityConfigUpdate,
  CapabilityId,
  JobList,
  Settings,
  SettingsUpdate,
  SpaceCreate,
} from "./types";

export const queryKeys = {
  setup: ["setup"] as const,
  settings: ["settings"] as const,
  capabilities: ["capabilities"] as const,
  spaces: ["spaces"] as const,
  space: (id: string) => ["spaces", id] as const,
  jobs: ["jobs"] as const,
  requestLog: ["request-log"] as const,
  calibration: ["calibration"] as const,
  sources: (sid: string) => ["spaces", sid, "sources"] as const,
  snapshot: (sid: string, sourceId: string) =>
    ["spaces", sid, "sources", sourceId, "snapshot"] as const,
  chunk: (sid: string, chunkId: string) => ["spaces", sid, "chunks", chunkId] as const,
  chunkContext: (sid: string, chunkId: string) =>
    ["spaces", sid, "chunks", chunkId, "context"] as const,
  secrets: (sid: string, sourceId: string) =>
    ["spaces", sid, "sources", sourceId, "secrets"] as const,
  chatStatus: (sid: string) => ["spaces", sid, "chat-status"] as const,
  conversations: (sid: string) => ["spaces", sid, "conversations"] as const,
  messages: (sid: string, cid: string) =>
    ["spaces", sid, "conversations", cid, "messages"] as const,
  overview: (sid: string) => ["spaces", sid, "overview"] as const,
  browse: (path: string) => ["browse", path] as const,
};

/** Jobs that still need work (shown in the header indicator). */
export function activeJobCount(data: JobList | undefined): number {
  if (!data) return 0;
  return data.summary.queued + data.summary.running;
}

export const JOBS_POLL_BUSY_MS = 3_000;
export const JOBS_POLL_IDLE_MS = 15_000;

// ---- Setup / settings -------------------------------------------------------

export function useSetupStatus() {
  return useQuery({ queryKey: queryKeys.setup, queryFn: api.getSetup, staleTime: 30_000 });
}

export function useCompleteSetup() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.completeSetup,
    onSuccess: (data) => {
      qc.setQueryData(queryKeys.setup, data);
      void qc.invalidateQueries({ queryKey: queryKeys.settings });
    },
  });
}

export function useSettings() {
  return useQuery({ queryKey: queryKeys.settings, queryFn: api.getSettings });
}

export function useUpdateSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: SettingsUpdate) => api.updateSettings(body),
    onSuccess: (data) => {
      qc.setQueryData(queryKeys.settings, data);
      void qc.invalidateQueries({ queryKey: queryKeys.jobs });
    },
  });
}

// ---- Capabilities -----------------------------------------------------------

export function useCapabilities() {
  return useQuery({ queryKey: queryKeys.capabilities, queryFn: api.listCapabilities });
}

export function useCapability(id: CapabilityId) {
  const query = useCapabilities();
  return { ...query, data: query.data?.find((c) => c.id === id) };
}

export function useUpdateCapability(id: CapabilityId) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: CapabilityConfigUpdate) => api.updateCapability(id, body),
    onSuccess: (updated) => {
      qc.setQueryData<Capability[]>(queryKeys.capabilities, (old) =>
        old ? old.map((c) => (c.id === updated.id ? updated : c)) : old,
      );
      // Other capabilities can depend on this one (gate mirrors chat), and setup readiness too.
      void qc.invalidateQueries({ queryKey: queryKeys.capabilities });
      void qc.invalidateQueries({ queryKey: queryKeys.setup });
    },
  });
}

export function useTestCapability(id: CapabilityId) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (draft: CapabilityConfigUpdate | undefined) => api.testCapability(id, draft ?? {}),
    onSuccess: (_result, draft) => {
      // A test without a draft is stored as last_test and may change the status.
      if (!draft || Object.keys(draft).length === 0) {
        void qc.invalidateQueries({ queryKey: queryKeys.capabilities });
      }
    },
    // The test sends a request to the endpoint, which the request log records (even on failure).
    onSettled: () => void qc.invalidateQueries({ queryKey: queryKeys.requestLog }),
  });
}

export function useListModels(id: CapabilityId) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (draft: CapabilityConfigUpdate) => api.listModels(id, draft),
    // Listing models calls the endpoint, which the request log records.
    onSettled: () => void qc.invalidateQueries({ queryKey: queryKeys.requestLog }),
  });
}

// ---- Spaces -----------------------------------------------------------------

export function useSpaces() {
  return useQuery({ queryKey: queryKeys.spaces, queryFn: api.listSpaces });
}

export function useSpace(id: string) {
  return useQuery({ queryKey: queryKeys.space(id), queryFn: () => api.getSpace(id) });
}

export function useCreateSpace() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: SpaceCreate) => api.createSpace(body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: queryKeys.spaces }),
  });
}

export function useDeleteSpace() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.deleteSpace(id),
    onSuccess: (_d, id) => {
      qc.removeQueries({ queryKey: queryKeys.space(id), exact: true });
      void qc.invalidateQueries({ queryKey: queryKeys.spaces });
      void qc.invalidateQueries({ queryKey: queryKeys.jobs });
    },
  });
}

// ---- Jobs -------------------------------------------------------------------

export function useJobs() {
  return useQuery({
    queryKey: queryKeys.jobs,
    queryFn: api.listJobs,
    refetchInterval: (query) =>
      activeJobCount(query.state.data) > 0 ? JOBS_POLL_BUSY_MS : JOBS_POLL_IDLE_MS,
    refetchIntervalInBackground: false,
  });
}

export function useJobAction() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, action }: { id: string; action: "cancel" | "pause" | "resume" }) => {
      if (action === "cancel") return api.cancelJob(id);
      if (action === "pause") return api.pauseJob(id);
      return api.resumeJob(id);
    },
    onSettled: () => void qc.invalidateQueries({ queryKey: queryKeys.jobs }),
  });
}

export function useReorderJobs() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (jobIds: string[]) => api.reorderJobs(jobIds),
    onSettled: () => void qc.invalidateQueries({ queryKey: queryKeys.jobs }),
  });
}

// ---- Request log / calibration ---------------------------------------------

export function useClearRequestLog() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.clearRequestLog,
    onSuccess: () => void qc.invalidateQueries({ queryKey: queryKeys.requestLog }),
  });
}

export function useCalibration() {
  return useQuery({
    queryKey: queryKeys.calibration,
    queryFn: api.getCalibration,
    refetchInterval: (query) => {
      const s = query.state.data?.status;
      return s === "running" || s === "pending" ? 2_000 : false;
    },
  });
}

export function useRunCalibration() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.runCalibration,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: queryKeys.calibration });
      void qc.invalidateQueries({ queryKey: queryKeys.jobs });
    },
  });
}

// ---- Sources ----------------------------------------------------------------

export const SOURCES_POLL_BUSY_MS = 2_000;
export const SOURCES_POLL_IDLE_MS = 15_000;

/** True while a source still has work to do (not ready and not failed). */
export function isSourceBusy(s: Source): boolean {
  return s.status !== "ready" && s.status !== "failed";
}

/** Every 2 s while any source is still being processed, else every 15 s. */
export function sourcesPollInterval(sources: Source[] | undefined): number {
  return sources?.some(isSourceBusy) ? SOURCES_POLL_BUSY_MS : SOURCES_POLL_IDLE_MS;
}

export function useSources(sid: string) {
  return useQuery({
    queryKey: queryKeys.sources(sid),
    queryFn: () => api.listSources(sid),
    refetchInterval: (query) => sourcesPollInterval(query.state.data),
    refetchIntervalInBackground: false,
  });
}

/** After anything that changes the source list: refresh it and what depends on it. */
function useInvalidateSources(sid: string) {
  const qc = useQueryClient();
  return () => {
    void qc.invalidateQueries({ queryKey: queryKeys.sources(sid) });
    void qc.invalidateQueries({ queryKey: queryKeys.chatStatus(sid) });
    void qc.invalidateQueries({ queryKey: queryKeys.overview(sid) });
    void qc.invalidateQueries({ queryKey: queryKeys.space(sid), exact: true });
    void qc.invalidateQueries({ queryKey: queryKeys.jobs });
  };
}

export function useAddPaste(sid: string) {
  const done = useInvalidateSources(sid);
  return useMutation({
    mutationFn: (body: PasteSourceCreate) => api.addPaste(sid, body),
    onSuccess: done,
  });
}

export function useUploadFiles(sid: string) {
  const done = useInvalidateSources(sid);
  return useMutation({
    mutationFn: (files: File[]) => api.uploadFiles(sid, files),
    onSuccess: done,
  });
}

export function useAddUrl(sid: string) {
  const done = useInvalidateSources(sid);
  return useMutation({ mutationFn: (url: string) => api.addUrl(sid, url), onSuccess: done });
}

export function useAddFolder(sid: string) {
  const done = useInvalidateSources(sid);
  return useMutation({ mutationFn: (path: string) => api.addFolder(sid, path), onSuccess: done });
}

export function useWebSearch(sid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ query, limit }: { query: string; limit?: number }) =>
      api.webSearch(sid, query, limit),
    // A search provider may be remote; the request log records it.
    onSettled: () => void qc.invalidateQueries({ queryKey: queryKeys.requestLog }),
  });
}

export function useAddFromSearch(sid: string) {
  const done = useInvalidateSources(sid);
  return useMutation({
    mutationFn: (items: SearchItem[]) => api.addFromSearch(sid, items),
    onSuccess: done,
  });
}

function mergeSources(old: Source[] | undefined, updated: Source[]): Source[] | undefined {
  if (!old) return old;
  const byId = new Map(updated.map((s) => [s.id, s]));
  return old.map((s) => byId.get(s.id) ?? s);
}

/** Ticks or unticks sources. Optimistic, so checkboxes respond at once. */
export function useSetSourcesEnabled(sid: string) {
  const qc = useQueryClient();
  const key = queryKeys.sources(sid);
  return useMutation({
    mutationFn: ({ ids, enabled }: { ids: string[]; enabled: boolean }) =>
      ids.length === 1
        ? api.updateSource(sid, ids[0] as string, { enabled }).then((s) => [s])
        : api.setSourcesEnabled(sid, ids, enabled),
    onMutate: async ({ ids, enabled }) => {
      await qc.cancelQueries({ queryKey: key });
      const previous = qc.getQueryData<Source[]>(key);
      const set = new Set(ids);
      qc.setQueryData<Source[]>(key, (old) =>
        old?.map((s) => (set.has(s.id) ? { ...s, enabled } : s)),
      );
      return { previous };
    },
    onError: (_e, _v, ctx) => {
      if (ctx?.previous) qc.setQueryData(key, ctx.previous);
    },
    onSuccess: (updated) => qc.setQueryData<Source[]>(key, (old) => mergeSources(old, updated)),
    onSettled: () => void qc.invalidateQueries({ queryKey: queryKeys.chatStatus(sid) }),
  });
}

export function useRefreshSource(sid: string) {
  const done = useInvalidateSources(sid);
  return useMutation({
    mutationFn: (sourceId: string) => api.refreshSource(sid, sourceId),
    onSuccess: done,
  });
}

export function useDeleteSource(sid: string) {
  const done = useInvalidateSources(sid);
  return useMutation({
    mutationFn: (sourceId: string) => api.deleteSource(sid, sourceId),
    onSuccess: done,
  });
}

export function useChunk(sid: string, chunkId: string | null) {
  return useQuery({
    queryKey: queryKeys.chunk(sid, chunkId ?? ""),
    queryFn: () => api.getChunk(sid, chunkId as string),
    enabled: chunkId !== null,
    staleTime: Infinity,
  });
}

/** What the source viewer shows for a cited chunk (code lines or text with context). */
export function useChunkContext(sid: string, chunkId: string | null) {
  return useQuery({
    queryKey: queryKeys.chunkContext(sid, chunkId ?? ""),
    queryFn: () => api.getChunkContext(sid, chunkId as string),
    enabled: chunkId !== null,
    staleTime: 60_000,
  });
}

/** The skipped-secrets report of a source; fetched only while the dialog is open. */
export function useSecretsReport(sid: string, sourceId: string, enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.secrets(sid, sourceId),
    queryFn: () => api.getSecrets(sid, sourceId),
    enabled,
    staleTime: 60_000,
  });
}

export function useSnapshot(sid: string, sourceId: string | null) {
  return useQuery({
    queryKey: queryKeys.snapshot(sid, sourceId ?? ""),
    queryFn: () => api.getSnapshot(sid, sourceId as string),
    enabled: sourceId !== null,
    staleTime: 60_000,
  });
}

export function useBrowse(path: string, enabled = true) {
  return useQuery({
    queryKey: queryKeys.browse(path),
    queryFn: () => api.browse(path),
    enabled,
    staleTime: 10_000,
  });
}

// ---- Chat -------------------------------------------------------------------

export function useChatStatus(sid: string) {
  return useQuery({ queryKey: queryKeys.chatStatus(sid), queryFn: () => api.getChatStatus(sid) });
}

export function useConversations(sid: string) {
  return useQuery({
    queryKey: queryKeys.conversations(sid),
    queryFn: () => api.listConversations(sid),
  });
}

export function useMessages(sid: string, cid: string | null) {
  return useQuery({
    queryKey: queryKeys.messages(sid, cid ?? ""),
    queryFn: () => api.listMessages(sid, cid as string),
    enabled: cid !== null,
  });
}

export function useDeleteConversation(sid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (cid: string) => api.deleteConversation(sid, cid),
    onSuccess: (_d, cid) => {
      qc.removeQueries({ queryKey: queryKeys.messages(sid, cid) });
      void qc.invalidateQueries({ queryKey: queryKeys.conversations(sid), exact: true });
    },
  });
}

// ---- Overview ---------------------------------------------------------------

export function useOverview(sid: string) {
  return useQuery({
    queryKey: queryKeys.overview(sid),
    queryFn: () => api.getOverview(sid),
    refetchInterval: (query) => (query.state.data?.status === "pending" ? 2_000 : false),
  });
}

export function useRefreshOverview(sid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.refreshOverview(sid),
    onSuccess: () => {
      // Show the skeleton at once; polling picks up the result.
      qc.setQueryData<Overview>(queryKeys.overview(sid), (old) =>
        old ? { ...old, status: "pending" } : old,
      );
      void qc.invalidateQueries({ queryKey: queryKeys.overview(sid) });
      void qc.invalidateQueries({ queryKey: queryKeys.jobs });
    },
  });
}

export function useSetOverviewEnabled(sid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (enabled: boolean) => api.setOverviewEnabled(sid, enabled),
    onSuccess: (data) => qc.setQueryData(queryKeys.overview(sid), data),
  });
}

// ---- Hints ------------------------------------------------------------------

/** Dismisses a one-time hint. Optimistic: the hint disappears at once. */
export function useDismissHint() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (hint: string) => api.updateSettings({ dismiss_hint: hint }),
    onMutate: (hint) => {
      qc.setQueryData<Settings>(queryKeys.settings, (old) =>
        old ? { ...old, hints_dismissed: [...(old.hints_dismissed ?? []), hint] } : old,
      );
    },
    onSuccess: (data) => qc.setQueryData(queryKeys.settings, data),
  });
}
