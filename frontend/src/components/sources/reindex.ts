import type { ReindexStatus, SourceEstimate } from "@/api/types";

/** "1 source needs it." / "3 sources need it." */
export function sourcesNeedIt(count: number): string {
  return count === 1 ? "1 source needs it." : `${count} sources need it.`;
}

/**
 * The estimate to confirm before starting a re-index, or null to start at once. Above two
 * minutes the user is asked first (SPEC §11), as for adding sources.
 */
export function reindexConfirmation(status: ReindexStatus): SourceEstimate | null {
  if (!status.confirm || !status.estimate_summary || status.estimate_seconds === null) return null;
  return {
    seconds: status.estimate_seconds,
    files: null,
    summary: status.estimate_summary,
    confirm: true,
  };
}
