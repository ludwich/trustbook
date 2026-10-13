import { IconRefresh } from "@tabler/icons-react";
import { useState } from "react";
import { Link } from "react-router";
import { useReindexStatuses, useSpaces, useStartReindex } from "@/api/queries";
import type { ReindexStatus, SourceEstimate, Space } from "@/api/types";
import { QueryError } from "@/components/common/QueryError";
import { EstimateConfirmDialog } from "@/components/sources/EstimateConfirmDialog";
import { reindexConfirmation, sourcesNeedIt } from "@/components/sources/reindex";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { errorMessage } from "@/lib/utils";
import { SettingsSection } from "./SettingsSection";

/**
 * Every space whose sources an older chunker indexed (D-106, D-114), with a Re-index button per
 * space. Uses the same per-space status and start as the banner in the space, so both agree.
 */
export function ReindexSection() {
  const spaces = useSpaces();
  const list = spaces.data ?? [];
  const statuses = useReindexStatuses(list.map((s) => s.id));
  const outdated = list.flatMap((space, i) => {
    const status = statuses[i]?.data;
    return status?.needed ? [{ space, status }] : [];
  });
  const loading = spaces.isPending || statuses.some((q) => q.isPending);
  const failed = statuses.filter((q) => q.isError);

  return (
    <SettingsSection
      id="reindex"
      title="Re-index outdated spaces"
      description="Spaces indexed with an older version show less accurate section labels until they are re-indexed. Chat keeps working meanwhile."
    >
      {spaces.isError && <QueryError error={spaces.error} onRetry={() => void spaces.refetch()} />}
      {failed.length > 0 && (
        <QueryError
          title="Could not check every space"
          error={failed[0]?.error}
          onRetry={() => failed.forEach((q) => void q.refetch())}
        />
      )}
      {loading && outdated.length === 0 && <Skeleton className="h-14" />}
      {!loading && !spaces.isError && failed.length === 0 && outdated.length === 0 && (
        <p className="text-sm text-muted-foreground" role="status">
          All spaces are up to date.
        </p>
      )}
      {outdated.length > 0 && (
        <ul className="divide-y rounded-lg border bg-card">
          {outdated.map(({ space, status }) => (
            <ReindexRow key={space.id} space={space} status={status} />
          ))}
        </ul>
      )}
    </SettingsSection>
  );
}

function ReindexRow({ space, status }: { space: Space; status: ReindexStatus }) {
  const start = useStartReindex(space.id);
  const [confirm, setConfirm] = useState<SourceEstimate | null>(null);
  const run = () => start.mutate();
  const onClick = () => {
    const ask = reindexConfirmation(status);
    if (ask) setConfirm(ask);
    else run();
  };

  return (
    <li
      className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 px-4 py-3"
      data-testid={`reindex-space-${space.id}`}
    >
      <div className="grid min-w-0 gap-0.5">
        <Link to={`/spaces/${space.id}`} className="font-medium break-words hover:underline">
          {space.name}
        </Link>
        {status.reindexing ? (
          <p className="flex items-center gap-1.5 text-sm text-muted-foreground">
            <Spinner label="Re-indexing" />
            <span>
              Re-indexing…{" "}
              <Link to="/queue" className="font-medium text-foreground underline">
                See progress in the queue
              </Link>
              .
            </span>
          </p>
        ) : (
          <p className="text-sm text-muted-foreground">
            {sourcesNeedIt(status.sources.length)}
            {status.estimate_summary ? ` ${status.estimate_summary}.` : ""}
          </p>
        )}
        {start.isError && (
          <p role="alert" className="text-sm text-destructive">
            Could not start re-indexing: {errorMessage(start.error)}
          </p>
        )}
      </div>
      {!status.reindexing && (
        <Button
          size="sm"
          variant="outline"
          onClick={onClick}
          disabled={start.isPending}
          aria-label={`Re-index ${space.name}`}
        >
          {start.isPending ? <Spinner label="Starting" /> : <IconRefresh aria-hidden />}
          Re-index
        </Button>
      )}
      <EstimateConfirmDialog
        estimate={confirm}
        actionLabel="Re-index"
        onAdd={() => {
          setConfirm(null);
          run();
        }}
        onCancel={() => setConfirm(null)}
      />
    </li>
  );
}
