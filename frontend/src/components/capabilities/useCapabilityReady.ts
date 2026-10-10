import { useCapabilities } from "@/api/queries";
import type { Capability, CapabilityId } from "@/api/types";

/** Whether a capability is usable right now (status "ready"). */
export function useCapabilityReady(id: CapabilityId): {
  ready: boolean;
  capability: Capability | undefined;
  isLoading: boolean;
} {
  const { data, isLoading } = useCapabilities();
  const capability = data?.find((c) => c.id === id);
  return { ready: capability?.status === "ready", capability, isLoading };
}
