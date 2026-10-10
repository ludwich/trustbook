import { useCallback, useRef, useState } from "react";
import { api } from "@/api/client";
import type { SourceEstimate, SourceEstimateItem } from "@/api/types";
import { EstimateConfirmDialog } from "./EstimateConfirmDialog";

interface Pending {
  estimate: SourceEstimate;
  proceed: () => void;
}

/**
 * Estimates before big adds (SPEC §11). `check(items, proceed)` asks the server how long the add
 * would take; above two minutes (`confirm`) it asks the user first, otherwise it adds at once.
 * If the estimate itself fails, the add goes ahead: an estimate must never block adding.
 */
export function useEstimateConfirm(spaceId: string) {
  const [pending, setPending] = useState<Pending | null>(null);
  const [checking, setChecking] = useState(false);
  const busy = useRef(false);

  const check = useCallback(
    async (items: SourceEstimateItem[], proceed: () => void, known?: SourceEstimate | null) => {
      if (busy.current) return;
      let estimate = known ?? null;
      if (!estimate) {
        busy.current = true;
        setChecking(true);
        try {
          estimate = await api.estimateSources(spaceId, items);
        } catch {
          estimate = null;
        } finally {
          busy.current = false;
          setChecking(false);
        }
      }
      if (estimate?.confirm) setPending({ estimate, proceed });
      else proceed();
    },
    [spaceId],
  );

  const dialog = (
    <EstimateConfirmDialog
      estimate={pending?.estimate ?? null}
      onAdd={() => {
        const p = pending;
        setPending(null);
        p?.proceed();
      }}
      onCancel={() => setPending(null)}
    />
  );

  return { check, checking, dialog };
}
