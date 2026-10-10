import type { CapabilityStatus } from "@/api/types";

export const STATUS_LABELS: Record<CapabilityStatus, string> = {
  ready: "Ready",
  not_configured: "Not set up",
  error: "Error",
  disabled: "Turned off",
};
