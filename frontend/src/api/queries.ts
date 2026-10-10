import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";
import type {
  Capability,
  CapabilityConfigUpdate,
  CapabilityId,
  JobList,
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
