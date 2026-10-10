import { IconSettings } from "@tabler/icons-react";
import type { ReactNode } from "react";
import { Link } from "react-router";
import type { CapabilityId } from "@/api/types";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { STATUS_LABELS } from "./statusLabels";
import { useCapabilityReady } from "./useCapabilityReady";

/**
 * Greys out a feature that needs a capability which is not working yet and points the user to
 * Settings. Reuse this wherever a feature depends on a capability (SPEC §6).
 *
 *   <CapabilityGate capability="chat" feature="Asking questions"> <ChatBox /> </CapabilityGate>
 */
export function CapabilityGate({
  capability,
  feature,
  children,
  className,
}: {
  capability: CapabilityId;
  /** What the user is trying to use, e.g. "Asking questions". */
  feature: string;
  children: ReactNode;
  className?: string;
}) {
  const { ready, capability: cap, isLoading } = useCapabilityReady(capability);

  // Don't flash the notice while loading; keep the feature inert until we know.
  if (ready) return <div className={className}>{children}</div>;

  return (
    <div className={cn("relative", className)} data-capability-gate={capability}>
      <div inert aria-hidden className="pointer-events-none opacity-45 grayscale select-none">
        {children}
      </div>
      {!isLoading && (
        <div className="absolute inset-0 flex items-center justify-center p-3">
          <div
            role="note"
            className="flex max-w-sm flex-col items-center gap-2 rounded-lg border bg-popover/95 px-4 py-3 text-center shadow-sm"
          >
            <p className="text-sm">
              <span className="font-medium">{feature}</span> needs the{" "}
              <span className="font-medium">{cap?.label ?? capability}</span>
              {cap ? ` (${STATUS_LABELS[cap.status].toLowerCase()})` : ""}.
            </p>
            {cap?.status_detail && (
              <p className="text-xs text-muted-foreground">{cap.status_detail}</p>
            )}
            <Button asChild size="sm" variant="outline">
              <Link to={`/settings#capability-${capability}`}>
                <IconSettings aria-hidden />
                Set up in Settings
              </Link>
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
