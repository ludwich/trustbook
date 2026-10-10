import type { ChatStatus } from "@/api/types";

/** Why asking is not possible right now, or null when it is. */
export function askBlocker(status: ChatStatus | undefined): string | null {
  if (!status) return "Loading…";
  if (!status.chat_ready) return "Set up in Settings";
  if (status.total_sources === 0) return "Add a source to start";
  if (status.ready_sources === 0)
    return "Your sources are still being read. You can ask once one is ready.";
  if (status.enabled_sources === 0) return "Tick at least one source to ask.";
  return null;
}
