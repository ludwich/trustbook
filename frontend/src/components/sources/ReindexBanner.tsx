import { IconRefresh } from "@tabler/icons-react";
import { useState } from "react";
import { Link } from "react-router";
import { useReindexStatus, useStartReindex } from "@/api/queries";
import type { SourceEstimate } from "@/api/types";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { errorMessage } from "@/lib/utils";
import { EstimateConfirmDialog } from "./EstimateConfirmDialog";
import { reindexConfirmation, sourcesNeedIt } from "./reindex";

/**
 * Tells the user that sources of this space were indexed by an older chunker (D-106) and offers
 * to re-index them. Never silent: the banner stays until every source is up to date. Progress is
 * in the queue; chat keeps using the current index until the new one is ready.
 */
export function ReindexBanner({ spaceId }: { spaceId: string }) {
  const status = useReindexStatus(spaceId);
  const start = useStartReindex(spaceId);
  const [confirm, setConfirm] = useState<SourceEstimate | null>(null);
  const data = status.data;
  if (!data?.needed) return null;

  if (data.reindexing) {
    return (
      <Alert variant="info" className="mb-2" data-testid="reindex-banner">
        <Spinner label="Re-indexing" />
        <AlertTitle>Re-indexing this space…</AlertTitle>
        <AlertDescription>
          <p>
            Chat keeps using the current index until it is done.{" "}
            <Link to="/queue" className="font-medium text-foreground underline">
              See progress in the queue
            </Link>
            .
          </p>
        </AlertDescription>
      </Alert>
    );
  }

  const run = () => start.mutate();
  const onClick = () => {
    const ask = reindexConfirmation(data);
    if (ask) setConfirm(ask);
    else run();
  };

  return (
    <Alert variant="warning" className="mb-2" data-testid="reindex-banner">
      <IconRefresh aria-hidden />
      <AlertTitle>This space was indexed with an older version.</AlertTitle>
      <AlertDescription>
        <p>
          Re-index to get accurate section labels. {sourcesNeedIt(data.sources.length)}
          {data.estimate_summary ? ` ${data.estimate_summary}.` : ""} Chat keeps working meanwhile.
        </p>
        {start.isError && (
          <p role="alert" className="text-destructive">
            Could not start re-indexing: {errorMessage(start.error)}
          </p>
        )}
        <div className="mt-1.5">
          <Button size="sm" onClick={onClick} disabled={start.isPending}>
            {start.isPending ? <Spinner label="Starting" /> : <IconRefresh aria-hidden />}
            Re-index
          </Button>
        </div>
      </AlertDescription>
      <EstimateConfirmDialog
        estimate={confirm}
        actionLabel="Re-index"
        onAdd={() => {
          setConfirm(null);
          run();
        }}
        onCancel={() => setConfirm(null)}
      />
    </Alert>
  );
}
